from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.types import OpportunityData
from app.models import FundingPayment, FundingPendingCycle, FundingSettlement, Position, TradeLog
from app.services.funding_metrics import apply_confirmed_metrics


class FundingLedger:
    """Account for confirmed funding only; unconfirmed cycles remain pending."""

    async def mark_position(
        self,
        session: AsyncSession,
        position: Position,
        opportunity: OpportunityData | None,
        now: datetime,
    ) -> None:
        effective_end = (
            self._aware(position.closed_at)
            if position.status != "open" and position.closed_at
            else now
        )
        current_cycle = self._cycle(effective_end)
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
            confirmed = await self._settle_cycle(session, position, cycle)
            if not confirmed:
                break
            position.last_funding_cycle = cycle
            cycle += timedelta(hours=1)

        has_confirmed = await session.scalar(
            select(FundingPayment.id)
            .where(
                FundingPayment.position_id == position.id,
                FundingPayment.rate_source == "exchange_history",
                FundingPayment.settlement_type == "confirmed",
            )
            .limit(1)
        )
        # A missing confirmation is not an estimate. It contributes zero to
        # P&L until the exchange history endpoint confirms the cycle.
        if position.status == "open" or has_confirmed is not None:
            position.accrued_long_funding_pnl_usd = 0.0
            position.accrued_short_funding_pnl_usd = 0.0
            position.accrued_funding_pnl_usd = 0.0
            position.long_funding_pnl_usd = position.settled_long_funding_pnl_usd or 0.0
            position.short_funding_pnl_usd = position.settled_short_funding_pnl_usd or 0.0
            position.funding_pnl_usd = (
                position.long_funding_pnl_usd + position.short_funding_pnl_usd
            )
        if opportunity is not None and (position.status == "open" or not position.closed_at):
            position.current_long_price = opportunity.long_mark_price
            position.current_short_price = opportunity.short_mark_price
            position.current_basis_bps = opportunity.basis_bps
            long_price = opportunity.long_mark_price
            short_price = opportunity.short_mark_price
            long_move = (long_price - position.long_entry_price) / position.long_entry_price
            short_move = (position.short_entry_price - short_price) / position.short_entry_price
            position.basis_pnl_usd = (long_move + short_move) * position.size_usd
        if position.status == "open" or has_confirmed is not None:
            await apply_confirmed_metrics(session, position)
        position.updated_at = now

    async def _settle_cycle(
        self,
        session: AsyncSession,
        position: Position,
        cycle: datetime,
    ) -> bool:
        existing = await session.scalar(
            select(FundingPayment).where(
                FundingPayment.position_id == position.id,
                FundingPayment.cycle_at == cycle,
                FundingPayment.rate_source == "exchange_history",
                FundingPayment.settlement_type == "confirmed",
            )
        )
        if existing is not None:
            return True

        long_settlement = await self._cycle_settlement(
            session, position.symbol, position.long_venue, cycle
        )
        short_settlement = await self._cycle_settlement(
            session, position.symbol, position.short_venue, cycle
        )
        if long_settlement is None or short_settlement is None:
            missing = []
            if long_settlement is None:
                missing.append(position.long_venue)
            if short_settlement is None:
                missing.append(position.short_venue)
            await self._mark_pending(session, position, cycle, ", ".join(missing))
            return False

        await session.execute(
            delete(FundingPendingCycle).where(
                FundingPendingCycle.position_id == position.id,
                FundingPendingCycle.cycle_at == cycle,
            )
        )
        leg_size = await self._leg_size_at_cycle(session, position, cycle)
        long_payment = -long_settlement.funding_rate * leg_size
        short_payment = short_settlement.funding_rate * leg_size
        session.add(
            FundingPayment(
                position_id=position.id,
                symbol=position.symbol,
                long_venue=position.long_venue,
                short_venue=position.short_venue,
                long_rate=long_settlement.funding_rate,
                short_rate=short_settlement.funding_rate,
                long_payment_usd=long_payment,
                short_payment_usd=short_payment,
                net_payment_usd=long_payment + short_payment,
                cycle_at=cycle,
                settlement_type="confirmed",
                rate_source="exchange_history",
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
        return True

    async def _leg_size_at_cycle(
        self, session: AsyncSession, position: Position, cycle: datetime
    ) -> float:
        logs = list(
            (
                await session.execute(
                    select(TradeLog).where(
                        TradeLog.position_id == position.id,
                        TradeLog.created_at <= cycle,
                    )
                )
            ).scalars()
        )
        opened = [log.size_usd for log in logs if log.phase == "open"]
        closed = [log.size_usd for log in logs if log.phase == "close"]
        if opened:
            opened_size = max(opened)
            close_at = self._aware(position.closed_at) if position.closed_at else None
            if (
                position.status != "open"
                and close_at is not None
                and cycle < close_at
                and abs((position.size_usd or 0.0) - opened_size) <= 1e-9
            ):
                return opened_size
            # Both legs of a paired fill are logged; use one leg's size for
            # the opening notional and half the close-log total for reductions.
            return max(0.0, opened_size - sum(closed) / 2.0)
        return position.leg_size_usd or position.size_usd

    async def _cycle_settlement(
        self,
        session: AsyncSession,
        symbol: str,
        venue: str,
        cycle: datetime,
    ) -> FundingSettlement | None:
        return await session.scalar(
            select(FundingSettlement).where(
                FundingSettlement.symbol == symbol.upper(),
                FundingSettlement.venue == venue.lower(),
                FundingSettlement.funding_cycle_at == cycle,
            )
        )

    async def _mark_pending(
        self,
        session: AsyncSession,
        position: Position,
        cycle: datetime,
        missing: str,
    ) -> None:
        pending = await session.scalar(
            select(FundingPendingCycle).where(
                FundingPendingCycle.position_id == position.id,
                FundingPendingCycle.cycle_at == cycle,
            )
        )
        now = datetime.now(timezone.utc)
        if pending is None:
            session.add(
                FundingPendingCycle(
                    position_id=position.id,
                    symbol=position.symbol,
                    long_venue=position.long_venue,
                    short_venue=position.short_venue,
                    cycle_at=cycle,
                    reason=f"awaiting exchange confirmation: {missing}",
                    first_seen_at=now,
                    updated_at=now,
                )
            )
        else:
            pending.reason = f"awaiting exchange confirmation: {missing}"
            pending.updated_at = now

    @staticmethod
    def _cycle(value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
