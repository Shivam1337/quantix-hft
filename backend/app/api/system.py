from fastapi import APIRouter, Request

from app.schemas import SystemMetricsRead
from app.services.system_metrics import collect_system_metrics

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/metrics", response_model=SystemMetricsRead)
async def get_system_metrics(request: Request) -> SystemMetricsRead:
    session_factory = request.app.state.session_factory
    cache = getattr(request.app.state, "cache", None)
    return await collect_system_metrics(session_factory, cache)
