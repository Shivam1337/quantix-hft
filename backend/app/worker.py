import asyncio
import logging

from app.cache import Cache
from app.config import get_settings
from app.database import create_database, initialize_database
from app.domain.calculator import CalculatorConfig
from app.domain.execution import ExecutionManager, PaperExecutionConfig
from app.domain.fees import FeeSchedule
from app.domain.risk import RiskConfig, RiskEngine
from app.exchanges.service import build_exchange_service
from app.services.alerts import AlertService
from app.services.market import MarketEngine
from app.services.orchestrator import Orchestrator
from app.services.positions import PositionService
from app.services.settings import SettingsService
from app.services.simulation import SimulationService

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    engine, sessions = create_database(settings.database_url)
    cache = Cache(settings.redis_url)
    await initialize_database(engine)
    risk = RiskEngine(
        RiskConfig(
            basis_threshold_bps=settings.basis_threshold_bps,
            negative_hours_to_unwind=settings.negative_hours_to_unwind,
            auto_unwind=settings.auto_unwind,
        )
    )
    alerts = AlertService(sessions, settings.alert_webhook_url)
    fee_schedule = FeeSchedule()
    settings_service = SettingsService(sessions, settings)
    market = MarketEngine(
        sessions,
        build_exchange_service(settings),
        cache,
        CalculatorConfig(
            fee_schedule=fee_schedule,
            capacity_fraction=settings.capacity_fraction,
            min_history_cycles=settings.entry_min_history_snapshots,
            expected_holding_hours=settings.entry_expected_holding_hours,
        ),
        settings_service=settings_service,
    )
    positions = PositionService(
        sessions,
        ExecutionManager(
            settings.live_trading_enabled,
            fee_schedule,
            PaperExecutionConfig(settings.paper_slippage_bps, settings.paper_post_only_fill_ratio),
        ),
        risk,
        alerts,
        settings_service=settings_service,
    )

    async def publish_market_update(message: dict) -> None:
        await cache.publish_json("market:updates", message)

    simulation = SimulationService(
        sessions,
        positions,
        settings_service,
        initial_balance_usd=settings.simulation_initial_balance_usd,
        leverage=settings.simulation_leverage,
    )
    orchestrator = Orchestrator(
        settings, market, positions, publish_market_update, simulation=simulation
    )
    orchestrator.start()
    logger.info("market worker started; polling every %ss", settings.market_poll_seconds)
    try:
        await asyncio.Event().wait()
    finally:
        await orchestrator.stop()
        await cache.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
