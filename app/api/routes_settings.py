"""
API Routes for System Settings and Trading Mode.
Exposes endpoints to view and update system configurations, Lighter API credentials,
and toggle runtime execution mode (SIMULATION, REAL, or DUAL).
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.settings_manager import settings_manager
from app.core.wallet_manager import wallet_manager

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class UpdateSettingsRequest(BaseModel):
    trading_mode: Optional[str] = None
    trading_enabled: Optional[bool] = None
    network: Optional[str] = None
    account_index: Optional[int] = None
    api_key_index: Optional[int] = None
    api_private_key: Optional[str] = None
    trade_margin_fraction: Optional[float] = None
    leverage: Optional[float] = None
    min_lag_trigger: Optional[float] = None
    minimum_net_profit_usd: Optional[float] = None
    max_hold_seconds: Optional[float] = None
    stop_loss_drawdown: Optional[float] = None
    simulation_starting_balance: Optional[float] = None


class SwitchModeRequest(BaseModel):
    mode: str


class TradingActivityRequest(BaseModel):
    enabled: bool


@router.get("", response_model=Dict[str, Any])
async def get_settings():
    """Returns current system settings and mode eligibility."""
    return settings_manager.get_summary(mask_keys=True)


@router.post("", response_model=Dict[str, Any])
async def update_settings(req: UpdateSettingsRequest):
    """Updates runtime configuration settings."""
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    success, msg = settings_manager.update_settings(payload)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    if req.account_index is not None:
        wallet_manager.set_lighter_account_index(req.account_index)

    return settings_manager.get_summary(mask_keys=True)


@router.post("/mode", response_model=Dict[str, Any])
async def switch_trading_mode(req: SwitchModeRequest):
    """Switches trading mode between SIMULATION, REAL, and DUAL."""
    success, msg = settings_manager.set_trading_mode(req.mode)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    return {
        "status": "success",
        "message": msg,
        "settings": settings_manager.get_summary(mask_keys=True),
    }


@router.post("/trading-activity", response_model=Dict[str, Any])
async def switch_trading_activity(req: TradingActivityRequest):
    """Immediately pause or resume new trading entries in either execution mode."""
    success, msg = settings_manager.set_trading_enabled(req.enabled)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {
        "status": "success",
        "message": msg,
        "settings": settings_manager.get_summary(mask_keys=True),
    }


@router.post("/reset-simulation", response_model=Dict[str, Any])
async def reset_simulation():
    """Resets paper trading history, engine stance, and starts a fresh simulation run."""
    from app.core.state_manager import state_manager
    result = await state_manager.reset_simulation()
    if result.get("status") != "ok":
        raise HTTPException(status_code=409, detail=result.get("message", "Simulation reset is unavailable."))
    return result
