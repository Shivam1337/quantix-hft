from fastapi import APIRouter, Query, Request

from app.api.position_schemas import FundingPaymentRead

funding_router = APIRouter(prefix="/api/v1", tags=["funding"])


@funding_router.get("/funding-payments", response_model=list[FundingPaymentRead])
async def get_funding_payments(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[FundingPaymentRead]:
    positions_service = request.app.state.positions
    payments = await positions_service.list_funding_payments(limit=limit)
    return [FundingPaymentRead.model_validate(p) for p in payments]
