import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entry import entry_rejection_reason, simulation_entry_rejection_reason
from app.domain.positioning import opportunity_for_position
from app.domain.types import OpportunityData
from app.models import Position, SimulationAccount, SimulationRun
from app.services.positions import PositionService
from app.services.settings import SettingsService
from app.services.simulation_accounts import (
    copy_account_to_run,
    ensure_account,
    new_run,
    reconcile_position,
    sync_account,
)

logger = logging.getLogger(__name__)


class SimulationService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        positions_service: PositionService,
        settings_service: SettingsService,
        initial_balance_usd: float = 1_000.0,
        leverage: float = 3.0,
    ):
        self.session_factory = session_factory
        self.positions_service = positions_service
        self.settings_service = settings_service
        self.initial_balance_usd = initial_balance_usd
        self.leverage = leverage

    async def get_or_create_account(self, session: AsyncSession) -> SimulationAccount:
        return await ensure_account(session, self.initial_balance_usd, self.leverage)

    async def get_account(self) -> SimulationAccount:
        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)
            await session.commit()
            await session.refresh(account)
            return account

    async def current_run_id(self) -> int | None:
        return (await self.get_account()).run_id

    async def sync_account_state(self) -> SimulationAccount:
        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)
            await sync_account(session, account)
            await session.commit()
            await session.refresh(account)
            return account

    async def reconcile_risk_closures(self, position_ids: list[str]) -> None:
        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)
            if position_ids:
                positions = list(
                    (
                        await session.execute(
                            select(Position).where(
                                Position.id.in_(position_ids), Position.status == "closed"
                            )
                        )
                    ).scalars()
                )
                now = datetime.now(timezone.utc)
                for position in positions:
                    await self.positions_service.ledger.mark_position(
                        session, position, None, now
                    )
                    await reconcile_position(session, account, position)
            await sync_account(session, account)
            await session.commit()

    async def reconcile_closed_funding(self, opportunities: list[OpportunityData]) -> None:
        """Retry closed positions so late exchange history changes P&L once."""
        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)
            positions = list(
                (
                    await session.execute(select(Position).where(Position.status == "closed"))
                ).scalars()
            )
            now = datetime.now(timezone.utc)
            for position in positions:
                opportunity = opportunity_for_position(position, opportunities)
                if opportunity is None:
                    continue
                await self.positions_service.ledger.mark_position(
                    session, position, opportunity, now
                )
                await reconcile_position(session, account, position)
            await sync_account(session, account)
            await session.commit()

    async def reset_simulation(self) -> SimulationAccount:
        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)
            previous = await session.get(SimulationRun, account.run_id)
            now = datetime.now(timezone.utc)
            if previous is not None:
                copy_account_to_run(account, previous)
                previous.status = "completed"
                previous.ended_at = now
            fresh = await new_run(
                session,
                SimulationAccount(
                    initial_balance=self.initial_balance_usd,
                    current_balance=self.initial_balance_usd,
                    allocated_balance=0.0,
                    total_realized_pnl=0.0,
                    leverage=self.leverage,
                ),
            )
            account.initial_balance = self.initial_balance_usd
            account.current_balance = self.initial_balance_usd
            account.allocated_balance = 0.0
            account.total_realized_pnl = 0.0
            account.leverage = self.leverage
            account.run_id = fresh.id
            account.updated_at = now
            await session.commit()
            await session.refresh(account)
            logger.info("simulation reset started run %s", fresh.run_number)
            return account

    async def evaluate_and_trade(self, opportunities: list[OpportunityData]) -> None:
        if not opportunities:
            return
        settings = await self.settings_service.get()
        values = list(opportunities)
        async with self.session_factory() as session:
            account = await self.get_or_create_account(session)
            open_positions = list(
                (
                    await session.execute(
                        select(Position).where(
                            Position.status == "open",
                            or_(
                                Position.simulation_run_id == account.run_id,
                                Position.simulation_run_id.is_(None),
                            ),
                        )
                    )
                ).scalars()
            )
            if open_positions or simulation_entry_rejection_reason(account, settings):
                await session.commit()
                return
            eligible = [item for item in values if entry_rejection_reason(item, settings) is None]
            if not eligible:
                await session.commit()
                return
            best = eligible[0]
            leg_margin_usd = account.current_balance / 2.0
            leg_size_usd = leg_margin_usd * self.leverage
            if best.capacity_usd > 0:
                leg_size_usd = min(leg_size_usd, best.capacity_usd)
            hist_desc = (
                f"confirmed history APR: {best.historical_3d_apr_pct:.1f}% "
                f"({best.historical_snapshots_count} snaps, "
                f"{best.spread_stability_pct:.0f}% stability)"
                if best.historical_3d_apr_pct is not None
                else "initial spot cycle"
            )
            open_reason = (
                f"Auto-opened: Top ranked spread {best.symbol} "
                f"({best.long_venue}/{best.short_venue}). Confirmed-rate APR: "
                f"{best.net_apr_pct:.1f}%, fee breakeven: "
                f"{best.fee_breakeven_hours or 0:.1f}h, expected hold: "
                f"{best.expected_holding_hours:.1f}h. Funding source: {hist_desc}."
            )
            await session.commit()
            position = await self.positions_service.open_position(
                best,
                leg_size_usd,
                paper=True,
                open_reason=open_reason,
                leg_size_usd=leg_size_usd,
                margin_per_leg_usd=leg_margin_usd,
                leverage=self.leverage,
                simulation_run_id=account.run_id,
            )
            account.allocated_balance = 2 * (position.margin_per_leg_usd or 0)
            run = await session.get(SimulationRun, account.run_id)
            if run:
                copy_account_to_run(account, run)
            await session.commit()
            logger.info("auto-opened position on %s", best.symbol)
