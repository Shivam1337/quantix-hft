"""
Trade Decisions & Sniper Execution Query Endpoints.
"""
from fastapi import APIRouter
from app.core.state_manager import state_manager

router = APIRouter(prefix="/api/trades", tags=["Trade Decisions & Execution"])


@router.get("/decision", summary="Query current trade decision & rationale")
async def get_current_trade_decision():
    """
    Returns the real-time trading stance (MONITORING, SIGNAL_DETECTED, IN_POSITION, COOLDOWN),
    action to take, target price, stop-loss price, and multi-sentence rationale explaining
    WHY the system is taking or rejecting trades.
    """
    summary = state_manager.sniper_engine.get_summary()
    return summary["decision"]


@router.get("/active", summary="Query currently open position")
async def get_active_position():
    """
    Returns details of any currently active trade position (entry price, target,
    stop-loss, floating PnL, duration) or null if idle.
    """
    summary = state_manager.sniper_engine.get_summary()
    return {"active_position": summary["active_position"]}


@router.get("/history", summary="Query closed trades history")
async def get_trades_history():
    """
    Returns recent closed trades with entry/exit prices, gross PnL, fees paid ($0.00),
    net PnL, hold duration, and exit trigger rationale.
    """
    summary = state_manager.sniper_engine.get_summary()
    return {"total_closed": len(summary["closed_trades"]), "trades": summary["closed_trades"]}


@router.get("/performance", summary="Query cumulative trading performance")
async def get_trading_performance():
    """
    Returns win rate, total net PnL, total trades count, average hold duration,
    and cumulative fees saved compared to fee-paying platforms (Polymarket).
    """
    return state_manager.sniper_engine.get_performance()
