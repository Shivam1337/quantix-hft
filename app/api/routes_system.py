"""
System Health, Connection Status, and SSE Streaming Endpoints.
"""
import json
import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.core.state_manager import state_manager

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


@router.get("/stream", summary="Live Server-Sent Events stream")
async def sse_event_stream():
    """
    Real-time Server-Sent Events (SSE) stream pushing comprehensive state
    snapshots to connected dashboard clients every 100ms.
    """
    async def event_generator():
        queue = asyncio.Queue()
        state_manager.sse_clients.add(queue)
        try:
            # Uvicorn's signal hook marks the manager before it waits for open
            # HTTP connections. Returning here releases dashboard SSE clients
            # promptly so the lifespan can drain PostgreSQL within Docker's
            # stop-grace budget.
            while not state_manager.is_shutting_down():
                # Push state every 100ms
                data = state_manager.get_full_state()
                yield f"data: {json.dumps(data)}\n\n"
                await asyncio.sleep(0.1)
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

