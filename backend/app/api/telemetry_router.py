from typing import Any

from fastapi import APIRouter, Request

from app.services.telemetry import telemetry

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


@router.get("/websocket-throughput")
async def get_websocket_throughput(request: Request) -> dict[str, Any]:
    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        try:
            cached = await cache.get_json("telemetry:throughput")
            if isinstance(cached, dict) and cached.get("venues"):
                return cached
        except Exception:
            pass
    return telemetry.get_throughput()
