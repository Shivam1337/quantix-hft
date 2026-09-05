"""
app/main.py
FastAPI application serving the real-time simulation API and interactive Web Dashboard.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import db
from app.models import (
    SQLQueryRequest, SQLQueryResponse, EngineControlRequest,
    PortfolioResponse, OpportunityResponse
)
from app.live_feed import live_feed
from app.simulator import simulator
from app.arb_engine import arb_engine
from app.query_service import execute_read_only_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB and start real-time feeds
    logger.info("Initializing Polymarket Real-Time Simulation Engine...")
    await db.initialize()
    await simulator.initialize()
    await live_feed.start()
    await arb_engine.start()
    yield
    # Graceful Shutdown
    logger.info("Shutting down engine gracefully...")
    await arb_engine.stop()
    await live_feed.stop()
    await db.close()
    logger.info("Engine shutdown complete.")


app = FastAPI(
    title="Polymarket Arbitrage Real-Time Simulation Engine",
    description="Production-grade real-time market data ingestion and simulated execution engine with PostgreSQL persistence.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for the dashboard
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Dashboard is loading...</h2>")


@app.get("/health")
async def health_check():
    """Liveness and readiness probe for Docker / Dokploy / Traefik."""
    try:
        val = await db.fetchval("SELECT 1")
        return {
            "status": "healthy",
            "database": "connected",
            "database_type": "PostgreSQL" if db.is_postgres else "SQLite",
            "engine_running": arb_engine.is_running,
            "uptime_seconds": round(time.time() - start_time, 1)
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Unhealthy: {str(e)}")


@app.get("/api/status")
async def get_status():
    uptime_sec = round(time.time() - start_time, 1)
    return {
        "status": "online",
        "database_type": "PostgreSQL" if db.is_postgres else "SQLite",
        "uptime_seconds": uptime_sec,
        "monitored_events_count": len(live_feed.monitored_events),
        "tracked_tokens_count": len(live_feed.order_books),
        "last_market_tick_ts": live_feed.last_update_ts,
        "is_engine_running": arb_engine.is_running
    }


@app.get("/api/portfolio", response_model=PortfolioResponse)
async def get_portfolio():
    return simulator.get_portfolio_summary()


@app.get("/api/opportunities")
async def get_opportunities():
    return arb_engine.recent_opportunities


@app.get("/api/positions")
async def get_positions():
    return list(simulator.open_positions.values())


@app.get("/api/trades")
async def get_trades(limit: int = 50):
    rows = await db.fetch(
        "SELECT * FROM simulated_trades ORDER BY executed_at DESC LIMIT $1",
        limit
    )
    return rows


@app.post("/api/query", response_model=SQLQueryResponse)
async def run_sql_query(payload: SQLQueryRequest):
    """
    Exposes an arbitrary read-only SQL query runner against PostgreSQL.
    Strictly validates that mutations (INSERT, UPDATE, DELETE, DROP) are rejected.
    """
    result = await execute_read_only_query(payload.query)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Query execution failed."))
    return result


@app.post("/api/control")
async def control_engine(payload: EngineControlRequest):
    action = payload.action.lower()
    if action == "pause":
        simulator.is_active = False
        return {"message": "Simulation paused."}
    elif action == "resume":
        simulator.is_active = True
        return {"message": "Simulation resumed."}
    elif action == "reset":
        await simulator.reset_simulation()
        return {"message": "Simulator reset to $50."}
    elif action == "set_threshold" and payload.value is not None:
        simulator.spread_threshold = payload.value
        return {"message": f"Spread threshold updated to {payload.value * 100:.2f}%"}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@app.get("/api/stream")
async def event_stream(request: Request):
    """Server-Sent Events (SSE) streaming real-time dashboard data to the browser."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            portfolio = simulator.get_portfolio_summary()
            payload = {
                "portfolio": portfolio,
                "opportunities": arb_engine.recent_opportunities[:15],
                "open_positions": list(simulator.open_positions.values()),
                "status": {
                    "events_count": len(live_feed.monitored_events),
                    "db_type": "PostgreSQL" if db.is_postgres else "SQLite",
                    "uptime": round(time.time() - start_time, 0)
                }
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
