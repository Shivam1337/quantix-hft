from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution import ExecutionManager, ExecutionResult
from app.domain.risk import RiskEngine
from app.domain.types import OpportunityData
from app.models import FundingPayment, Position, TradeLog
from app.services.alerts import AlertService


class PositionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        execution: ExecutionManager,
        risk: RiskEngine,
        alerts: AlertService,
    ):
        self.session_factory = session_factory
        self.execution = execution
        self.risk = risk
        self.alerts = alerts

    async def open_position(
        self,
        opportunity: OpportunityData,
        size_usd: float,
        paper: bool,
        open_reason: str | None = None,
        leg_size_usd: float | None = None,
    ) -> Position:
        result = self.execution.open_pair(opportunity, size_usd, paper)
        now = datetime.now(timezone.utc)
        position = Position(
            id=str(uuid4()),
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            long_venue=opportunity.long_venue,
            short_venue=opportunity.short_venue,
            size_usd=size_usd,
            leg_size_usd=leg_size_usd or size_usd,
            open_reason=open_reason,
            long_entry_price=opportunity.long_mark_price,
            short_entry_price=opportunity.short_mark_price,
            entry_basis_bps=opportunity.basis_bps,
            current_long_price=opportunity.long_mark_price,
            current_short_price=opportunity.short_mark_price,
            current_basis_bps=opportunity.basis_bps,
            funding_pnl_usd=0,
            long_funding_pnl_usd=0,
            short_funding_pnl_usd=0,
            last_funding_cycle=now.replace(minute=0, second=0, microsecond=0),
            basis_pnl_usd=0,
            entry_fee_usd=sum(leg.fee_usd for leg in result.legs),
            exit_fee_usd=0,
            fees_usd=sum(leg.fee_usd for leg in result.legs),
            status="open",
            negative_hours=0,
            opened_at=now,
            updated_at=now,
        )
        async with self.session_factory() as session:
            session.add(position)
            self._add_trade_logs(session, position.id, result)
            await session.commit()
        return position

    async def close_position(self, position_id: str, reason: str = "manual close") -> Position:
        return await self._close_position(position_id, reason, None)

    async def close_position_with_market(
        self,
        position_id: str,
        opportunity: OpportunityData | None,
        reason: str = "manual close",
    ) -> Position:
        return await self._close_position(position_id, reason, opportunity)

    async def get_position(self, position_id: str) -> Position | None:
        async with self.session_factory() as session:
            return await session.get(Position, position_id)

    async def _close_position(
        self,
        position_id: str,
        reason: str,
        opportunity: OpportunityData | None,
    ) -> Position:
        async with self.session_factory() as session:
            position = await session.get(Position, position_id)
            if position is None:
                raise KeyError("position not found")
            if position.status != "open":
                raise ValueError("position is already closed")
            result = (
                self.execution.close_pair(opportunity, position.size_usd)
                if opportunity
                else self.execution.close_pair_for_venues(
                    position.long_venue,
                    position.short_venue,
                    position.long_entry_price,
                    position.short_entry_price,
                    position.size_usd,
                )
            )
            self._mark_closed(position, reason)
            position.exit_fee_usd = sum(leg.fee_usd for leg in result.legs)
            position.fees_usd += position.exit_fee_usd
            self._add_trade_logs(session, position.id, result)
            await session.commit()
            return position

    async def list_positions(self, active_only: bool = True) -> list[Position]:
        async with self.session_factory() as session:
            statement = select(Position).order_by(Position.opened_at.desc())
            if active_only:
                statement = statement.where(Position.status == "open")
            return list((await session.execute(statement)).scalars())

    async def list_funding_payments(self, limit: int = 100) -> list[FundingPayment]:
        async with self.session_factory() as session:
            stmt = select(FundingPayment).order_by(FundingPayment.cycle_at.desc(), FundingPayment.id.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars())

    async def evaluate_risk(self, opportunities: Iterable[OpportunityData]) -> list[str]:
        by_id = {item.id: item for item in opportunities}
        now = datetime.now(timezone.utc)
        events: list[tuple[str, str, str]] = []
        closed_ids: list[str] = []
        async with self.session_factory() as session:
            positions = list(
                (await session.execute(select(Position).where(Position.status == "open"))).scalars()
            )
            for position in positions:
                opportunity = by_id.get(position.opportunity_id)
                if opportunity is None:
                    continue
                self._accrue(position, opportunity, now, session)
                decision = self.risk.evaluate(
                    opportunity.net_apr_pct, opportunity.basis_bps, position.negative_hours
                )
                if decision.should_unwind:
                    self._mark_closed(position, decision.reason or "risk guard")
                    result = self.execution.close_pair(opportunity, position.size_usd)
                    position.exit_fee_usd = sum(leg.fee_usd for leg in result.legs)
                    position.fees_usd += position.exit_fee_usd
                    self._add_trade_logs(session, position.id, result)
                    events.append(("risk_guard", decision.reason or "risk guard", position.id))
                    closed_ids.append(position.id)
            await session.commit()
        for event, message, position_id in events:
            await self.alerts.send(event, message, position_id)
        return closed_ids

    @staticmethod
    def _accrue(
        position: Position,
        opportunity: OpportunityData,
        now: datetime,
        session: AsyncSession | None = None,
    ) -> None:
        previous = PositionService._aware(position.updated_at)
        elapsed_hours = max(0, (now - previous).total_seconds() / 3600)
        leg_size = position.leg_size_usd or position.size_usd
        long_pay = -opportunity.long_funding_rate * leg_size * elapsed_hours
        short_pay = opportunity.short_funding_rate * leg_size * elapsed_hours
        position.long_funding_pnl_usd = (position.long_funding_pnl_usd or 0.0) + long_pay
        position.short_funding_pnl_usd = (position.short_funding_pnl_usd or 0.0) + short_pay
        position.funding_pnl_usd = position.long_funding_pnl_usd + position.short_funding_pnl_usd
        long_move = (
            opportunity.long_mark_price - position.long_entry_price
        ) / position.long_entry_price
        short_move = (
            position.short_entry_price - opportunity.short_mark_price
        ) / position.short_entry_price
        position.basis_pnl_usd = (long_move + short_move) * position.size_usd
        position.current_long_price = opportunity.long_mark_price
        position.current_short_price = opportunity.short_mark_price
        position.current_basis_bps = opportunity.basis_bps
        current_cycle = now.replace(minute=0, second=0, microsecond=0)
        last_cycle = PositionService._aware(position.last_funding_cycle) if position.last_funding_cycle else None
        if session is not None and last_cycle is not None and current_cycle > last_cycle:
            cycle_hrs = max(1.0, (current_cycle - last_cycle).total_seconds() / 3600)
            c_l = -opportunity.long_funding_rate * leg_size * cycle_hrs
            c_s = opportunity.short_funding_rate * leg_size * cycle_hrs
            session.add(
                FundingPayment(
                    position_id=position.id,
                    symbol=position.symbol,
                    long_venue=position.long_venue,
                    short_venue=position.short_venue,
                    long_rate=opportunity.long_funding_rate,
                    short_rate=opportunity.short_funding_rate,
                    long_payment_usd=c_l,
                    short_payment_usd=c_s,
                    net_payment_usd=c_l + c_s,
                    cycle_at=current_cycle,
                )
            )
            position.last_funding_cycle = current_cycle
        elif position.last_funding_cycle is None:
            position.last_funding_cycle = current_cycle
        hour_key = now.strftime("%Y-%m-%dT%H")
        if opportunity.net_apr_pct < 0:
            if position.last_negative_hour != hour_key:
                position.negative_hours += 1
                position.last_negative_hour = hour_key
        else:
            position.negative_hours = 0
            position.last_negative_hour = None
        position.updated_at = now

    @staticmethod
    def _mark_closed(position: Position, reason: str) -> None:
        position.status = "closed"
        position.closed_at = datetime.now(timezone.utc)
        position.updated_at = position.closed_at
        position.close_reason = reason

    @staticmethod
    def _add_trade_logs(session: AsyncSession, position_id: str, result: ExecutionResult) -> None:
        for leg in result.legs:
            session.add(
                TradeLog(
                    position_id=position_id,
                    venue=leg.venue,
                    side=leg.side,
                    order_type=leg.order_type,
                    size_usd=leg.size_usd,
                    price=leg.price,
                    status="paper_filled" if result.paper else "filled",
                    client_order_id=leg.client_order_id,
                    phase=leg.phase,
                    fee_bps=leg.fee_bps,
                    fee_usd=leg.fee_usd,
                )
            )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
