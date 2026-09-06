"""
System Health, Connection Status, and SSE Streaming Endpoints.
"""
import json
import asyncio
import time
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.core.state_manager import state_manager
from app.core.dashboard_payload import (
    DASHBOARD_DETAIL_INTERVAL_SECONDS,
    DASHBOARD_TICK_INTERVAL_SECONDS,
    build_dashboard_detail,
    build_dashboard_snapshot,
    build_dashboard_tick,
)

router = APIRouter(prefix="/api/system", tags=["System & Health"])


@router.get("/health", summary="Query engine health & performance")
async def get_system_health():
    """
    Returns system uptime, WebSocket connection states, total messages parsed,
    and instantaneous tick rate in Hz.
    """
    return state_manager.get_health()


@router.get("/connections", summary="Query active WebSocket connections")
async def get_connections():
    """
    Returns granular status for each exchange feed.
    """
    health = state_manager.get_health()
    return {
        "connections": health["feeds"],
        "feed_ages_ms": health["feed_ages_ms"],
        "stale_feeds": health["stale_feeds"],
        "active_sse_clients": health["active_sse_clients"],
    }


@router.get("/providers", summary="Query provider roles, freshness, and update activity")
async def get_provider_insights():
    """Returns source-specific measurement context without changing market state."""
    return state_manager.get_provider_insights()


@router.get("/resources", summary="Query host and process CPU/RAM usage")
async def get_resource_usage():
    """CPU percentages use the same total-machine-capacity scale for comparison."""
    return state_manager.get_resource_usage()


@router.get("/persistence", summary="Query persistence health")
async def get_persistence_status():
    """Shows writer health and explicitly confirms raw feed messages are not retained."""
    return state_manager.get_persistence_status()


@router.get("/database-size", summary="Query total database size on disk without polling")
async def get_database_size():
    """Returns database size in bytes, MB, and GB. Intended to be called on demand / refresh."""
    return await state_manager.get_database_size()


@router.get("/readiness", summary="Deployment readiness without external-feed dependency")
async def get_readiness():
    """Used by Docker health checks; requires PostgreSQL and a non-shutting-down app."""
    return state_manager.get_readiness()


@router.get("/dashboard", summary="Query the bounded dashboard control-plane snapshot")
async def get_dashboard_snapshot():
    """Returns the compact initial payload used by the dashboard SSE client."""
    return build_dashboard_snapshot(state_manager)


def _sse_event(name: str, payload: dict) -> str:
    """Serialize a named SSE frame without JSON whitespace overhead."""
    return f"event: {name}\ndata:{json.dumps(payload, separators=(',', ':'))}\n\n"


@router.get("/stream", summary="Live Server-Sent Events stream")
async def sse_event_stream():
    """
    Stream a bounded dashboard snapshot followed by compact control ticks.

    The dashboard does not need full chart history, L2 books, and table rows
    ten times per second. It receives responsive market/decision updates at
    4 Hz and chart/table/resource details at 1 Hz instead.
    """
    async def event_generator():
        queue = asyncio.Queue()
        state_manager.sse_clients.add(queue)
        try:
            # Uvicorn's signal hook marks the manager before it waits for open
            # HTTP connections. Returning here releases dashboard SSE clients
            # promptly so the lifespan can drain PostgreSQL within Docker's
            # stop-grace budget.
            yield _sse_event("snapshot", build_dashboard_snapshot(state_manager))
            last_detail_at = time.monotonic()
            while not state_manager.is_shutting_down():
                yield _sse_event("tick", build_dashboard_tick(state_manager))
                now = time.monotonic()
                if now - last_detail_at >= DASHBOARD_DETAIL_INTERVAL_SECONDS:
                    yield _sse_event("detail", build_dashboard_detail(state_manager))
                    last_detail_at = now
                await asyncio.sleep(DASHBOARD_TICK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass
        finally:
            state_manager.sse_clients.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.post("/reset-simulation", summary="Reset simulation state to start from zero")
async def reset_simulation():
    """Resets the simulation: wipes paper trades, decision stance, and resets starting balance."""
    return await state_manager.reset_simulation()
