from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution import ExecutionManager
from app.domain.pnl import basis_bps, basis_pnl
from app.domain.positioning import opportunity_for_position
from app.domain.risk import RiskConfig, RiskEngine
from app.domain.types import OpportunityData
from app.models import FundingPayment, Position
from app.services.alerts import AlertService
from app.services.funding_ledger import FundingLedger
from app.services.partial_close import apply_close_result
from app.services.settings import SettingsService
from app.services.trade_logs import add_trade_logs


class PositionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        execution: ExecutionManager,
        risk: RiskEngine,
        alerts: AlertService,
        ledger: FundingLedger | None = None,
        settings_service: SettingsService | None = None,
    ):
        self.session_factory = session_factory
        self.execution = execution
        self.risk = risk
        self.alerts = alerts
        self.ledger = ledger or FundingLedger()
        self.settings_service = settings_service

    async def open_position(
        self,
        opportunity: OpportunityData,
        size_usd: float,
        paper: bool,
        open_reason: str | None = None,
        leg_size_usd: float | None = None,
        margin_per_leg_usd: float | None = None,
        leverage: float = 1.0,
        simulation_run_id: int | None = None,
    ) -> Position:
        result = self.execution.open_pair(opportunity, size_usd, paper)
        filled_size = result.matched_size_usd
        if filled_size <= 0:
            raise ValueError("paper execution produced no matched fill")
        long_leg, short_leg = result.legs
        requested_size = result.requested_size_usd or size_usd
        fill_ratio = filled_size / requested_size
        effective_margin = (
            margin_per_leg_usd * fill_ratio
            if margin_per_leg_usd is not None
            else filled_size / max(leverage, 1.0)
        )
        now = datetime.now(timezone.utc)
        position = Position(
            id=str(uuid4()),
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            long_venue=opportunity.long_venue,
            short_venue=opportunity.short_venue,
            size_usd=filled_size,
            leg_size_usd=filled_size,
            open_reason=open_reason,
            long_entry_price=long_leg.price,
            short_entry_price=short_leg.price,
            entry_basis_bps=basis_bps(long_leg.price, short_leg.price),
            current_long_price=opportunity.long_mark_price,
            current_short_price=opportunity.short_mark_price,
            current_basis_bps=opportunity.basis_bps,
            margin_per_leg_usd=effective_margin,
            leverage=leverage,
            funding_pnl_usd=0,
            long_funding_pnl_usd=0,
            short_funding_pnl_usd=0,
            settled_funding_pnl_usd=0,
            settled_long_funding_pnl_usd=0,
            settled_short_funding_pnl_usd=0,
            accrued_funding_pnl_usd=0,
            accrued_long_funding_pnl_usd=0,
            accrued_short_funding_pnl_usd=0,
            accounted_net_pnl_usd=0,
            last_funding_cycle=now.replace(minute=0, second=0, microsecond=0),
            accrual_started_at=now,
            basis_pnl_usd=basis_pnl(
                long_leg.price,
                short_leg.price,
                opportunity.long_mark_price,
                opportunity.short_mark_price,
                filled_size,
            ),
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
            entry_rate_observed_at=opportunity.funding_history_latest_cycle,
            # These fields are populated only after a confirmed settlement.
            # The opportunity rates are rolling medians, not current funding.
            last_net_apr_pct=None,
            last_long_funding_rate=None,
            last_short_funding_rate=None,
            last_rate_observed_at=None,
            simulation_run_id=simulation_run_id,
            requested_size_usd=requested_size,
            execution_fill_ratio=fill_ratio,
            temporary_exposure_usd=result.temporary_exposure_usd,
        )
        async with self.session_factory() as session:
            session.add(position)
            add_trade_logs(session, position.id, result, symbol=opportunity.symbol)
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
                    symbol=position.symbol,
                )
            )
            apply_close_result(position, result, reason)
            add_trade_logs(session, position.id, result, symbol=position.symbol)
            await session.commit()
            return position

    async def list_positions(
        self, active_only: bool = True, simulation_run_id: int | None = None
    ) -> list[Position]:
        async with self.session_factory() as session:
            statement = select(Position).order_by(Position.opened_at.desc())
            if active_only:
                statement = statement.where(Position.status == "open")
            if simulation_run_id is not None:
                statement = statement.where(Position.simulation_run_id == simulation_run_id)
            return list((await session.execute(statement)).scalars())

    async def list_funding_payments(self, limit: int = 100) -> list[FundingPayment]:
        async with self.session_factory() as session:
            stmt = (
                select(FundingPayment)
                .where(
                    FundingPayment.rate_source == "exchange_history",
                    FundingPayment.settlement_type == "confirmed",
                )
                .order_by(FundingPayment.cycle_at.desc(), FundingPayment.id.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars())

    async def evaluate_risk(
        self,
        opportunities: Iterable[OpportunityData],
        simulation_run_id: int | None = None,
    ) -> list[str]:
        values = list(opportunities)
        now = datetime.now(timezone.utc)
        active_settings = await self.settings_service.get() if self.settings_service else None
        risk_config = (
            RiskConfig(
                basis_threshold_bps=active_settings.basis_threshold_bps,
                negative_hours_to_unwind=active_settings.negative_hours_to_unwind,
                auto_unwind=active_settings.auto_unwind,
            )
            if active_settings
            else None
        )
        events: list[tuple[str, str, str]] = []
        closed_ids: list[str] = []
        async with self.session_factory() as session:
            statement = select(Position).where(Position.status == "open")
            if simulation_run_id is not None:
                statement = statement.where(Position.simulation_run_id == simulation_run_id)
            positions = list((await session.execute(statement)).scalars())
            for position in positions:
                opportunity = opportunity_for_position(position, values)
                if opportunity is None:
                    continue
                await self.ledger.mark_position(session, position, opportunity, now)
                decision = self.risk.evaluate(
                    position.last_net_apr_pct,
                    opportunity.basis_bps,
                    position.negative_hours,
                    config=risk_config,
                )
                if decision.should_unwind:
                    result = self.execution.close_pair(opportunity, position.size_usd)
                    close_reason = decision.reason or "risk guard"
                    apply_close_result(position, result, close_reason)
                    add_trade_logs(session, position.id, result, symbol=position.symbol)
                    events.append(("risk_guard", close_reason, position.id))
                    if position.status == "closed":
                        closed_ids.append(position.id)
            await session.commit()
        for event, message, position_id in events:
            await self.alerts.send(event, message, position_id)
        return closed_ids
