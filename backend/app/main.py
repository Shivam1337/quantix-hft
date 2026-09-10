import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.api.diagnostics import router as diagnostics_router
from app.api.exchanges import router as exchanges_router
from app.api.funding import funding_router
from app.api.routes import router
from app.api.simulation import router as simulation_router
from app.api.system import router as system_router
from app.api.telemetry_router import router as telemetry_router
from app.api.wallet import router as wallet_router
from app.api.websocket import ConnectionManager, serve_market_socket
from app.cache import Cache
from app.config import Settings, get_settings
from app.database import create_database, initialize_database
from app.domain.calculator import CalculatorConfig
from app.domain.execution import ExecutionManager, PaperExecutionConfig
from app.domain.fees import FeeSchedule
from app.domain.risk import RiskConfig, RiskEngine
from app.exchanges.service import ExchangeService, build_exchange_service
from app.services.alerts import AlertService
from app.services.market import MarketEngine
from app.services.orchestrator import Orchestrator
from app.services.positions import PositionService
from app.services.settings import SettingsService
from app.services.simulation import SimulationService
from app.services.wallet import WalletService

logging.basicConfig(level=logging.INFO)


async def relay_market_updates(
    cache: Cache, market: MarketEngine, manager: ConnectionManager
) -> None:
    async for message in cache.subscribe_json("market:updates"):
        if not isinstance(message, dict):
            continue
        data = message.get("data", {})
        if isinstance(data, dict):
            if isinstance(data.get("opportunities"), list):
                market.apply_cached_opportunities(data["opportunities"])
            if isinstance(data.get("snapshots"), list):
                market.apply_cached_snapshots(data["snapshots"])
        await manager.broadcast(message)


def create_app(
    app_settings: Settings | None = None,
    exchange_service: ExchangeService | None = None,
    wallet_service: WalletService | None = None,
) -> FastAPI:
    settings = app_settings or get_settings()
    engine, sessions = create_database(settings.database_url)
    cache = Cache(settings.redis_url)
    manager = ConnectionManager()
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
    active_exchange_service = exchange_service or build_exchange_service(settings)
    active_wallet_service = wallet_service or WalletService(settings.live_trading_enabled)
    market = MarketEngine(
        sessions,
        active_exchange_service,
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

    async def deliver_market_update(message: dict) -> None:
        if settings.enable_scheduler:
            await manager.broadcast(message)
        else:
            await cache.publish_json("market:updates", message)

    simulation = SimulationService(
        sessions,
        positions,
        settings_service,
        initial_balance_usd=settings.simulation_initial_balance_usd,
        leverage=settings.simulation_leverage,
    )
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
            close_exchange = getattr(active_exchange_service, "close", None)
            if close_exchange is not None:
                await close_exchange()
            close_wallet = getattr(active_wallet_service, "close", None)
            if close_wallet is not None:
                await close_wallet()
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
    app.state.wallet = active_wallet_service
    app.include_router(router)
    app.include_router(simulation_router)
    app.include_router(funding_router)
    app.include_router(system_router)
    app.include_router(exchanges_router)
    app.include_router(telemetry_router)
    app.include_router(diagnostics_router)
    app.include_router(wallet_router)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs", "health": "/api/v1/health"}

    @app.websocket("/ws/market")
    async def market_socket(websocket: WebSocket) -> None:
        await serve_market_socket(websocket, manager)

    return app


app = create_app()
