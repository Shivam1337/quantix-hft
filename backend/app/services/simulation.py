import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entry import entry_rejection_reason
from app.domain.positioning import opportunity_for_position
from app.domain.types import OpportunityData
from app.models import FundingPayment, Position, SimulationAccount, TradeLog
from app.services.positions import PositionService
from app.services.settings import SettingsService

logger = logging.getLogger(__name__)


class SimulationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        positions_service: PositionService,
        settings_service: SettingsService,
    ):
        self.session_factory = session_factory
        self.positions_service = positions_service
        self.settings_service = settings_service

    async def get_or_create_account(self, session: AsyncSession) -> SimulationAccount:
        account = await session.get(SimulationAccount, 1)
        if account is None:
            account = SimulationAccount(
                id=1,
                initial_balance=10_000.0,
                current_balance=10_000.0,
                allocated_balance=0.0,
                total_realized_pnl=0.0,
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
        return account

    async def get_account(self) -> SimulationAccount:
        async with self.session_factory() as session:
            return await self.get_or_create_account(session)

    async def reconcile_risk_closures(self, position_ids: list[str]) -> None:
        """Move newly risk-closed positions into the paper account."""
        if not position_ids:
            return
        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)
            closed = list(
                (
                    await session.execute(
                        select(Position).where(
                            Position.id.in_(position_ids), Position.status == "closed"
                        )
                    )
                ).scalars()
            )
            if not closed:
                return
            net_pnl = sum(
                position.funding_pnl_usd + position.basis_pnl_usd - position.fees_usd
                for position in closed
            )
            account.current_balance += net_pnl
            account.total_realized_pnl += net_pnl
            active = list(
                (
                    await session.execute(
                        select(Position).where(Position.status == "open")
                    )
                ).scalars()
            )
            account.allocated_balance = sum(
                2 * (position.leg_size_usd or position.size_usd) for position in active
            )
            await session.commit()

    async def reset_simulation(self) -> SimulationAccount:
        async with self.session_factory() as session:
            await session.execute(delete(TradeLog))
            await session.execute(delete(FundingPayment))
            await session.execute(delete(Position))
            account = await self.get_or_create_account(session)
            account.current_balance = account.initial_balance
            account.allocated_balance = 0.0
            account.total_realized_pnl = 0.0
            account.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(account)
            logger.info(
                "simulation reset completed: balance restored to $%.2f",
                account.initial_balance,
            )
            return account

    async def evaluate_and_trade(self, opportunities: list[OpportunityData]) -> None:
        if not opportunities:
            return

        settings = await self.settings_service.get()
        values = list(opportunities)
        open_positions = await self.positions_service.list_positions(active_only=True)

        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)

            # 1. Autonomous exit evaluation
            for pos in list(open_positions):
                opp = opportunity_for_position(pos, values)
                if not opp:
                    continue

                close_trigger = None
                if (
                    opp.net_apr_pct >= 0
                    and opp.historical_3d_apr_pct is not None
                    and opp.historical_3d_apr_pct < 0
                ):
                    close_trigger = (
                        "Auto-closed: 3-day historical APR degraded to "
                        f"{opp.historical_3d_apr_pct:.2f}%"
                    )
                elif abs(opp.basis_bps) > settings.basis_threshold_bps:
                    close_trigger = (
                        "Auto-closed: Basis widened to "
                        f"{opp.basis_bps:.1f} bps "
                        f"(threshold: {settings.basis_threshold_bps:.1f})"
                    )
                elif pos.negative_hours >= 2:
                    close_trigger = (
                        "Auto-closed: Negative funding persisted for "
                        f"{pos.negative_hours} consecutive hours"
                    )

                if close_trigger:
                    closed = await self.positions_service.close_position_with_market(
                        pos.id, opp, reason=close_trigger
                    )
                    net_pnl = closed.funding_pnl_usd + closed.basis_pnl_usd - closed.fees_usd
                    account.current_balance += net_pnl
                    account.total_realized_pnl += net_pnl
                    account.allocated_balance = 0.0
                    await session.commit()
                    logger.info(
                        "auto-closed position %s: %s, net pnl: $%.2f",
                        pos.id,
                        close_trigger,
                        net_pnl,
                    )
                    open_positions = [p for p in open_positions if p.id != pos.id]

            # 2. Autonomous entry evaluation (only when no open positions)
            if not open_positions and account.current_balance >= 100:
                eligible = [
                    item
                    for item in values
                    if entry_rejection_reason(item, settings) is None
                ]

                if eligible:
                    # Pick best opportunity considering historical carry
                    best = eligible[0]
                    # System takes account balance, divides it in half, and uses it on each leg
                    leg_size_usd = account.current_balance / 2.0
                    if best.capacity_usd > 0:
                        leg_size_usd = min(leg_size_usd, best.capacity_usd)

                    if leg_size_usd >= 50:
                        hist_desc = (
                            f"confirmed history APR: {best.historical_3d_apr_pct:.1f}% "
                            f"({best.historical_snapshots_count} snaps, "
                            f"{best.spread_stability_pct:.0f}% stability)"
                            if best.historical_3d_apr_pct is not None
                            else "initial spot cycle"
                        )
                        open_reason = (
                            f"Auto-opened: Top ranked spread {best.symbol} "
                            f"({best.long_venue}/{best.short_venue}). "
                            f"Confirmed-rate APR: {best.net_apr_pct:.1f}%, "
                            f"Basis: {best.basis_bps:.1f} bps. "
                            f"Funding source: {hist_desc}. "
                            f"Sized at ${leg_size_usd:,.2f}/leg (50% of "
                            f"${account.current_balance:,.2f} account balance)."
                        )

                        await self.positions_service.open_position(
                            opportunity=best,
                            size_usd=leg_size_usd,
                            paper=True,
                            open_reason=open_reason,
                            leg_size_usd=leg_size_usd,
                        )
                        account.allocated_balance = leg_size_usd * 2.0
                        await session.commit()
                        logger.info("auto-opened position on %s: %s", best.symbol, open_reason)
