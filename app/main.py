"""
FastAPI Application Entrypoint.
Initializes lifespan background WebSocket tasks, registers API routers, and serves static dashboard UI.
"""
import os
import sys
import logging
import asyncio
from contextlib import asynccontextmanager
from aiohttp import ClientSession
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# Configure UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.config import DEFAULT_HEADERS
from app.core.market_feed import (
    binance_ws_task,
    bybit_ws_task,
    okx_ws_task,
    hyperliquid_ws_task,
    lighter_ws_task,
    polymarket_ws_task
)
from app.core.state_manager import state_manager
from app.core.real_account_refresh import real_account_refresh_task
from app.api.routes_market import router as market_router
from app.api.routes_trades import router as trades_router
from app.api.routes_analytics import router as analytics_router
from app.api.routes_system import router as system_router
from app.api.routes_wallet import router as wallet_router
from app.api.routes_settings import router as settings_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages background native WebSocket feed lifecycles across application startup and shutdown.
    Streams 6 venues concurrently: Binance, Bybit, OKX, Hyperliquid, Polymarket (discovery) and Lighter.xyz (execution).
    """
    logger.info("Initializing 6-Exchange Native WebSocket feeds (Binance + Bybit + OKX + HL + Lighter + Poly)...")
    await state_manager.initialize_persistence()
    logger.info("%s derived-state persistence is ready.", state_manager.persistence_backend_name)
    session = ClientSession(headers=DEFAULT_HEADERS)

    tasks = [
        asyncio.create_task(binance_ws_task(session)),
        asyncio.create_task(bybit_ws_task(session)),
        asyncio.create_task(okx_ws_task(session)),
        asyncio.create_task(hyperliquid_ws_task(session)),
        asyncio.create_task(lighter_ws_task(session)),
        asyncio.create_task(polymarket_ws_task(session)),
        asyncio.create_task(real_account_refresh_task()),
    ]


    try:
        yield
    finally:
        state_manager.begin_shutdown()
        logger.info("Graceful shutdown started: stopping market-feed intake...")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not session.closed:
            await session.close()
        logger.info("Graceful shutdown: flushing derived %s records...", state_manager.persistence_backend_name)
        await state_manager.shutdown()
        logger.info("Graceful shutdown complete: feeds stopped and persistence drained.")



app = FastAPI(
    title="BTC Perpetual Lead-Lag Measurement Experiment",
    description="Paper-only asynchronous market measurement for testing whether Lighter follows major-venue BTC moves.",
    version="2.3.0",
    lifespan=lifespan,
)

# The experiment is local by default; permit only its local dashboard origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Static Files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount API Routers
app.include_router(market_router)
app.include_router(trades_router)
app.include_router(analytics_router)
app.include_router(system_router)
app.include_router(wallet_router)
app.include_router(settings_router)


@app.get("/", include_in_schema=False)
async def serve_dashboard():
    """Serves the main real-time dashboard UI."""
    return FileResponse(os.path.join(static_dir, "index.html"))
