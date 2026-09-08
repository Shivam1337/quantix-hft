import asyncio
import logging

from app.cache import Cache
from app.config import get_settings
from app.database import create_database, initialize_database
from app.domain.calculator import CalculatorConfig
from app.domain.execution import ExecutionManager
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
            auto_unwind=settings.auto_unwind,
        )
    )
    alerts = AlertService(sessions, settings.alert_webhook_url)
    fee_schedule = FeeSchedule()
    market = MarketEngine(
        sessions,
        build_exchange_service(settings),
        cache,
        CalculatorConfig(
            fee_schedule=fee_schedule,
            capacity_fraction=settings.capacity_fraction,
        ),
    )
    positions = PositionService(
        sessions, ExecutionManager(settings.live_trading_enabled, fee_schedule), risk, alerts
    )

    async def publish_market_update(message: dict) -> None:
        await cache.publish_json("market:updates", message)

    settings_service = SettingsService(sessions, settings)
    simulation = SimulationService(sessions, positions, settings_service)
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
