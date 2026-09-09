from fastapi import APIRouter, HTTPException, Request

from app.schemas import WalletImportRequest, WalletRead
from app.services.wallet_models import WalletValidationError

router = APIRouter(prefix="/api/v1/wallet", tags=["wallet"])


@router.get("", response_model=WalletRead)
async def get_wallet(request: Request) -> WalletRead:
    return WalletRead.model_validate(request.app.state.wallet.snapshot())


@router.post("/import", response_model=WalletRead)
async def import_wallet(request: Request, body: WalletImportRequest) -> WalletRead:
    try:
        await request.app.state.wallet.import_key(body.private_key.get_secret_value())
    except WalletValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WalletRead.model_validate(await request.app.state.wallet.refresh())


@router.post("/refresh", response_model=WalletRead)
async def refresh_wallet(request: Request) -> WalletRead:
    return WalletRead.model_validate(await request.app.state.wallet.refresh())


@router.delete("", response_model=WalletRead)
async def clear_wallet(request: Request) -> WalletRead:
    return WalletRead.model_validate(await request.app.state.wallet.clear())
