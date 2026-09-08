from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.types import OpportunityData
from app.models import FundingPayment, FundingSnapshot, Position


class FundingLedger:
    """Maintain settled funding rows and a separate current-cycle estimate."""

    async def mark_position(
        self,
        session: AsyncSession,
        position: Position,
        opportunity: OpportunityData,
        now: datetime,
    ) -> None:
        current_cycle = self._cycle(now)
        last_cycle = (
            self._aware(position.last_funding_cycle)
            if position.last_funding_cycle
            else None
        )
        if last_cycle is None:
            last_cycle = current_cycle
            position.last_funding_cycle = current_cycle

        cycle = last_cycle + timedelta(hours=1)
        while cycle <= current_cycle:
            await self._settle_cycle(session, position, opportunity, cycle)
            position.last_funding_cycle = cycle
            cycle += timedelta(hours=1)

        start = self._aware(position.accrual_started_at) if position.accrual_started_at else now
        start = max(start, current_cycle)
        elapsed_hours = max(0.0, (now - start).total_seconds() / 3600)
        leg_size = position.leg_size_usd or position.size_usd
        position.accrued_long_funding_pnl_usd = (
            -opportunity.long_funding_rate * leg_size * elapsed_hours
        )
        position.accrued_short_funding_pnl_usd = (
            opportunity.short_funding_rate * leg_size * elapsed_hours
        )
        position.accrued_funding_pnl_usd = (
            position.accrued_long_funding_pnl_usd + position.accrued_short_funding_pnl_usd
        )
        position.long_funding_pnl_usd = (
            (position.settled_long_funding_pnl_usd or 0.0)
            + position.accrued_long_funding_pnl_usd
        )
        position.short_funding_pnl_usd = (
            (position.settled_short_funding_pnl_usd or 0.0)
            + position.accrued_short_funding_pnl_usd
        )
        position.funding_pnl_usd = position.long_funding_pnl_usd + position.short_funding_pnl_usd
        position.current_long_price = opportunity.long_mark_price
        position.current_short_price = opportunity.short_mark_price
        position.current_basis_bps = opportunity.basis_bps
        long_move = (
            opportunity.long_mark_price - position.long_entry_price
        ) / position.long_entry_price
        short_move = (
            position.short_entry_price - opportunity.short_mark_price
        ) / position.short_entry_price
        position.basis_pnl_usd = (long_move + short_move) * position.size_usd
        position.last_net_apr_pct = opportunity.net_apr_pct
        position.last_long_funding_rate = opportunity.long_funding_rate
        position.last_short_funding_rate = opportunity.short_funding_rate
        position.last_rate_observed_at = opportunity.observed_at
        self._update_negative_hours(position, opportunity.net_apr_pct, now)
        position.updated_at = now

    async def _settle_cycle(
        self,
        session: AsyncSession,
        position: Position,
        opportunity: OpportunityData,
        cycle: datetime,
    ) -> None:
        existing = await session.scalar(
            select(FundingPayment).where(
                FundingPayment.position_id == position.id,
                FundingPayment.cycle_at == cycle,
            )
        )
        if existing is not None:
            return

        long_rate, long_source = await self._cycle_rate(
            session, position.symbol, position.long_venue, cycle, opportunity.long_funding_rate
        )
        short_rate, short_source = await self._cycle_rate(
            session, position.symbol, position.short_venue, cycle, opportunity.short_funding_rate
        )
        leg_size = position.leg_size_usd or position.size_usd
        long_payment = -long_rate * leg_size
        short_payment = short_rate * leg_size
        source = (
            "market_snapshot"
            if long_source == short_source == "market_snapshot"
            else "current_estimate"
        )
        session.add(
            FundingPayment(
                position_id=position.id,
                symbol=position.symbol,
                long_venue=position.long_venue,
                short_venue=position.short_venue,
                long_rate=long_rate,
                short_rate=short_rate,
                long_payment_usd=long_payment,
                short_payment_usd=short_payment,
                net_payment_usd=long_payment + short_payment,
                cycle_at=cycle,
                settlement_type="simulated",
                rate_source=source,
            )
        )
        position.settled_long_funding_pnl_usd = (
            position.settled_long_funding_pnl_usd or 0.0
        ) + long_payment
        position.settled_short_funding_pnl_usd = (
            position.settled_short_funding_pnl_usd or 0.0
        ) + short_payment
        position.settled_funding_pnl_usd = (
            position.settled_long_funding_pnl_usd + position.settled_short_funding_pnl_usd
        )
        position.accrual_started_at = cycle

    async def _cycle_rate(
        self,
        session: AsyncSession,
        symbol: str,
        venue: str,
        cycle: datetime,
        fallback: float,
    ) -> tuple[float, str]:
        snapshot = await session.scalar(
            select(FundingSnapshot)
            .where(
                FundingSnapshot.symbol == symbol,
                FundingSnapshot.venue == venue,
                FundingSnapshot.funding_cycle_at == cycle,
            )
            .order_by(FundingSnapshot.observed_at.desc())
        )
        if snapshot is None:
            return fallback, "current_estimate"
        return snapshot.funding_rate, "market_snapshot"

    @staticmethod
    def _update_negative_hours(position: Position, net_apr_pct: float, now: datetime) -> None:
        hour_key = now.strftime("%Y-%m-%dT%H")
        if net_apr_pct < 0:
            if position.last_negative_hour != hour_key:
                position.negative_hours += 1
                position.last_negative_hour = hour_key
        else:
            position.negative_hours = 0
            position.last_negative_hour = None

    @staticmethod
    def _cycle(value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
