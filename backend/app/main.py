import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.api.diagnostics import router as diagnostics_router
from app.api.exchanges import router as exchanges_router
from app.api.routes import router
from app.api.system import router as system_router
from app.api.telemetry_router import router as telemetry_router
from app.api.websocket import ConnectionManager, serve_market_socket
from app.cache import Cache
from app.config import Settings, get_settings
from app.database import create_database, initialize_database
from app.domain.calculator import CalculatorConfig
from app.domain.execution import ExecutionManager
from app.domain.fees import FeeSchedule
from app.domain.risk import RiskConfig, RiskEngine
from app.exchanges.service import ExchangeService, build_exchange_service
from app.services.alerts import AlertService
from app.services.market import MarketEngine
from app.services.orchestrator import Orchestrator
from app.services.positions import PositionService
from app.services.settings import SettingsService
from app.services.simulation import SimulationService

logging.basicConfig(level=logging.INFO)


async def relay_market_updates(
    cache: Cache, market: MarketEngine, manager: ConnectionManager
) -> None:
    async for message in cache.subscribe_json("market:updates"):
        if not isinstance(message, dict):
            continue
        data = message.get("data", {})
        if isinstance(data, dict) and isinstance(data.get("opportunities"), list):
            market.apply_cached_opportunities(data["opportunities"])
        await manager.broadcast(message)


def create_app(
    app_settings: Settings | None = None,
    exchange_service: ExchangeService | None = None,
) -> FastAPI:
    settings = app_settings or get_settings()
    engine, sessions = create_database(settings.database_url)
    cache = Cache(settings.redis_url)
    manager = ConnectionManager()
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
        exchange_service or build_exchange_service(settings),
        cache,
        CalculatorConfig(
            fee_schedule=fee_schedule,
            capacity_fraction=settings.capacity_fraction,
        ),
    )
    positions = PositionService(
        sessions, ExecutionManager(settings.live_trading_enabled, fee_schedule), risk, alerts
    )

    async def deliver_market_update(message: dict) -> None:
        if settings.enable_scheduler:
            await manager.broadcast(message)
        else:
            await cache.publish_json("market:updates", message)

    settings_service = SettingsService(sessions, settings)
    simulation = SimulationService(sessions, positions, settings_service)
    orchestrator = Orchestrator(
        settings, market, positions, deliver_market_update, simulation=simulation
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for attempt in range(10):
            try:
                await initialize_database(engine)
                break
            except Exception:
                if attempt == 9:
                    raise
                await asyncio.sleep(2)
        await settings_service.get()
        bridge_task = None
        if settings.enable_scheduler:
            orchestrator.start()
        else:
            bridge_task = asyncio.create_task(
                relay_market_updates(cache, market, manager), name="market-update-bridge"
            )
        try:
            yield
        finally:
            if bridge_task:
                bridge_task.cancel()
                await asyncio.gather(bridge_task, return_exceptions=True)
            await orchestrator.stop()
            await cache.close()
            await engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.db_engine = engine
    app.state.session_factory = sessions
    app.state.cache = cache
    app.state.manager = manager
    app.state.risk = risk
    app.state.alerts = alerts
    app.state.market = market
    app.state.positions = positions
    app.state.orchestrator = orchestrator
    app.state.settings_service = settings_service
    app.state.simulation = simulation
    app.include_router(router)
    app.include_router(system_router)
    app.include_router(exchanges_router)
    app.include_router(telemetry_router)
    app.include_router(diagnostics_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs", "health": "/api/v1/health"}

    @app.websocket("/ws/market")
    async def market_socket(websocket: WebSocket) -> None:
        await serve_market_socket(websocket, manager)

    return app


app = create_app()
