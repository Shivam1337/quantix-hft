from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution import ExecutionManager, ExecutionResult
from app.domain.positioning import opportunity_for_position
from app.domain.risk import RiskEngine
from app.domain.types import OpportunityData
from app.models import FundingPayment, Position, TradeLog
from app.services.alerts import AlertService
from app.services.funding_ledger import FundingLedger


class PositionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        execution: ExecutionManager,
        risk: RiskEngine,
        alerts: AlertService,
        ledger: FundingLedger | None = None,
    ):
        self.session_factory = session_factory
        self.execution = execution
        self.risk = risk
        self.alerts = alerts
        self.ledger = ledger or FundingLedger()

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
            settled_funding_pnl_usd=0,
            settled_long_funding_pnl_usd=0,
            settled_short_funding_pnl_usd=0,
            accrued_funding_pnl_usd=0,
            accrued_long_funding_pnl_usd=0,
            accrued_short_funding_pnl_usd=0,
            last_funding_cycle=now.replace(minute=0, second=0, microsecond=0),
            accrual_started_at=now,
            basis_pnl_usd=0,
            entry_fee_usd=sum(leg.fee_usd for leg in result.legs),
            exit_fee_usd=0,
            fees_usd=sum(leg.fee_usd for leg in result.legs),
            status="open",
            negative_hours=0,
            opened_at=now,
            updated_at=now,
            entry_net_apr_pct=opportunity.net_apr_pct,
            entry_historical_apr_pct=opportunity.historical_3d_apr_pct,
            entry_long_funding_rate=opportunity.long_funding_rate,
            entry_short_funding_rate=opportunity.short_funding_rate,
            entry_rate_observed_at=opportunity.observed_at,
            last_net_apr_pct=opportunity.net_apr_pct,
            last_long_funding_rate=opportunity.long_funding_rate,
            last_short_funding_rate=opportunity.short_funding_rate,
            last_rate_observed_at=opportunity.observed_at,
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
            if opportunity is not None:
                await self.ledger.mark_position(
                    session, position, opportunity, datetime.now(timezone.utc)
                )
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
            stmt = (
                select(FundingPayment)
                .order_by(FundingPayment.cycle_at.desc(), FundingPayment.id.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars())

    async def evaluate_risk(self, opportunities: Iterable[OpportunityData]) -> list[str]:
        values = list(opportunities)
        now = datetime.now(timezone.utc)
        events: list[tuple[str, str, str]] = []
        closed_ids: list[str] = []
        async with self.session_factory() as session:
            positions = list(
                (await session.execute(select(Position).where(Position.status == "open"))).scalars()
            )
            for position in positions:
                opportunity = opportunity_for_position(position, values)
                if opportunity is None:
                    continue
                await self.ledger.mark_position(session, position, opportunity, now)
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
