"""
Market State & Price Query API Endpoints.
"""
from fastapi import APIRouter
from app.core.state_manager import state_manager

router = APIRouter(prefix="/api/market", tags=["Market Data"])


@router.get("/state", summary="Query complete market state")
async def get_market_state():
    """
    Returns full real-time snapshot of Hyperliquid, Lighter.xyz, and Polymarket,
    including best bids, asks, spreads, lag measurements, and active trades.
    """
    return state_manager.get_full_state()


@router.get("/prices", summary="Query lightweight live prices")
async def get_market_prices():
    """
    Fast, lightweight endpoint returning only mid prices, top quotes, and lag differentials.
    """
    return state_manager.get_prices()


@router.get("/orderbooks", summary="Query top order book depth")
async def get_orderbooks():
    """
    Returns top 6 bid and ask levels for Hyperliquid, Lighter, and Polymarket.
    """
    return state_manager.get_orderbooks()


@router.get("/history", summary="Query rolling price chart history")
async def get_price_history():
    """
    Returns up to 300 gap-aware, rate-limited price samples for all six providers.
    """
    full = state_manager.get_full_state()
    return full.get("chart", {})
