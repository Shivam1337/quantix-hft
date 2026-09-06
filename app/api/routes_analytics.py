"""
Lead-Lag Dynamics & Arbitrage Analytics Endpoints.
"""
from fastapi import APIRouter
from app.core.state_manager import state_manager

router = APIRouter(prefix="/api/analytics", tags=["Lead-Lag Analytics"])


@router.get("/lead-lag", summary="Query who is leading and who is lagging")
async def get_lead_lag_status():
    """
    Returns the latest market-update snapshot. Reading this endpoint never updates
    histories, basis values, or lead-lag event state.
    """
    return state_manager.lead_lag_analyzer.get_latest()


@router.get("/repricing-events", summary="Query recent repricing catch-up events")
async def get_repricing_events():
    """
    Returns observed spread-closure events. `resolution_type` identifies whether
    Lighter moved, the leader reversed, a basis shift occurred, or the move was mixed.
    """
    events = state_manager.lead_lag_analyzer.get_repricing_events()
    return {"total_events": len(events), "events": events}


@router.get("/experiment-status", summary="Inspect lead-lag experiment integrity")
async def get_experiment_status():
    """Shows the evidence logger and the safeguards applied to paper signals."""
    return state_manager.get_experiment_status()


@router.get("/fees-comparison", summary="Compare exchange fee hurdles")
async def get_fees_comparison():
    """
    Detailed economic breakdown comparing round-trip trading costs on Lighter.xyz,
    Binance, Bybit, OKX, Hyperliquid, and Polymarket at current BTC prices.
    """
    ref_mid = (
        state_manager.binance["mid_price"]
        or state_manager.bybit["mid_price"]
        or state_manager.okx["mid_price"]
        or state_manager.hl["mid_price"]
        or 80000.0
    )
    return {
        "paper_only": True,
        "execution_notice": "Fee figures are reference assumptions only. They do not prove an executable or profitable trade.",
        "reference_btc_price": ref_mid,
        "lighter_xyz": {
            "fee_rate": "0.000% (Zero Fee)",
            "round_trip_cost_per_btc": "$0.00",
            "minimum_move_to_profit": "$0.10 (Spread only)",
            "verdict": "Paper-model reference only. Validate fills, spread, latency, funding, and market impact before assigning profitability.",
        },
        "binance_futures": {
            "fee_rate": "Taker 0.050% (0.100% round-trip)",
            "round_trip_cost_per_btc": f"${ref_mid * 0.0010:,.2f}",
            "minimum_move_to_profit": f"${ref_mid * 0.0010:,.2f} + spread",
            "verdict": "Global #1 liquidity discovery venue. Discovery signal only.",
        },
        "bybit_linear": {
            "fee_rate": "Taker 0.055% (0.110% round-trip)",
            "round_trip_cost_per_btc": f"${ref_mid * 0.0011:,.2f}",
            "minimum_move_to_profit": f"${ref_mid * 0.0011:,.2f} + spread",
            "verdict": "Global retail & institutional derivative momentum leader. Discovery signal only.",
        },
        "okx_perpetual": {
            "fee_rate": "Taker 0.050% (0.100% round-trip)",
            "round_trip_cost_per_btc": f"${ref_mid * 0.0010:,.2f}",
            "minimum_move_to_profit": f"${ref_mid * 0.0010:,.2f} + spread",
            "verdict": "Top-tier Asian orderbook depth. Discovery signal only.",
        },
        "hyperliquid": {
            "fee_rate": "Taker 0.045% (0.090% round-trip)",
            "round_trip_cost_per_btc": f"${ref_mid * 0.0009:,.2f}",
            "minimum_move_to_profit": f"${ref_mid * 0.0009:,.2f} + spread",
            "verdict": "Largest on-chain perp DEX discovery engine. Used for consensus confirmation.",
        },
        "polymarket_perps": {
            "fee_rate": "Taker 0.040% (0.080% round-trip)",
            "round_trip_cost_per_btc": f"${ref_mid * 0.0008:,.2f}",
            "minimum_move_to_profit": f"${ref_mid * 0.0008:,.2f} + spread",
            "verdict": f"Moves under ${ref_mid * 0.0008:,.1f} lose money to fees.",
        },
    }
