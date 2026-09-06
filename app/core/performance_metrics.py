"""Pure performance payload generation for simulation and confirmed real trades."""
from typing import Any, Dict, Iterable, Mapping, Optional


COMPARATOR_ROUND_TRIP_FEE_RATE = 0.0008


def build_performance(
    *,
    closed_trades: Iterable[Mapping[str, Any]],
    active_trade: Optional[Mapping[str, Any]],
    mode: str,
    leverage: float,
    margin_fraction: float,
    simulation_balance: float,
    real_account: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return mode-scoped performance without mixing simulation and real fills."""
    is_real = mode == "REAL"
    trades = [
        trade for trade in closed_trades
        if (str(trade.get("mode", "")).upper() == "REAL") == is_real
    ]
    net_pnl = round(sum(_number(trade.get("net_pnl")) for trade in trades), 2)
    gross_pnl = round(sum(_number(trade.get("gross_pnl")) for trade in trades), 2)
    account = _account_values(is_real, active_trade, simulation_balance, real_account)
    configured_target_margin = account["equity"] * margin_fraction
    if account["available"]:
        target_margin = min(configured_target_margin, account["free_margin"]) if is_real else configured_target_margin
        target_margin = round(target_margin, 2)
    else:
        target_margin = 0.0
    target_notional = round(target_margin * leverage, 2)
    total = len(trades)
    wins = sum(1 for trade in trades if bool(trade.get("is_win")))
    losses = total - wins
    gross_wins = sum(_number(trade.get("gross_pnl")) for trade in trades if _number(trade.get("gross_pnl")) > 0)
    gross_losses = abs(sum(_number(trade.get("gross_pnl")) for trade in trades if _number(trade.get("gross_pnl")) < 0))
    fees_saved = round(sum(_estimated_round_trip_fee(trade) for trade in trades), 2)
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total) * 100, 1) if total else 0.0,
        "gross_pnl": gross_pnl,
        "fees_saved_vs_poly": fees_saved,
        "fees_saved_rate_pct": COMPARATOR_ROUND_TRIP_FEE_RATE * 100,
        "net_pnl": net_pnl,
        "avg_hold_sec": round(sum(_number(trade.get("hold_sec")) for trade in trades) / total, 1) if total else 0.0,
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses else (99.0 if gross_wins > 0 else 0.0),
        "account_base_balance_usd": account["base_balance"],
        "account_balance_usd": account["equity"],
        "account_equity_usd": account["equity"],
        "account_collateral_usd": account["collateral"],
        "account_unrealized_pnl_usd": account["unrealized_pnl"],
        "account_position_notional_usd": account["position_notional"],
        "margin_used_usd": account["margin_used"],
        "free_margin_usd": account["free_margin"],
        "leverage": leverage,
        "margin_utilization_pct": round((account["margin_used"] / account["equity"]) * 100, 1) if account["equity"] > 0 else 0.0,
        "target_margin_usd": target_margin,
        "configured_target_margin_usd": round(configured_target_margin, 2),
        "target_margin_fraction_pct": round(margin_fraction * 100, 1),
        "target_notional_usd": target_notional,
        "return_on_margin_pct": round((net_pnl / target_margin) * 100, 2) if target_margin > 0 else 0.0,
        "trading_mode": mode,
        "is_real_mode": is_real,
        "paper_only": not is_real,
        "account_data_available": account["available"],
        "metrics_scope": "CONFIRMED_REAL_STRATEGY" if is_real else "SIMULATION",
        "cost_model": "Confirmed Lighter account and IOC fills; no Lighter fee is applied." if is_real else "Displayed L2-ladder paper model with 50x leverage on Lighter.xyz (0% fees).",
    }


def _account_values(
    is_real: bool,
    active_trade: Optional[Mapping[str, Any]],
    simulation_balance: float,
    real_account: Mapping[str, Any],
) -> Dict[str, Any]:
    if is_real:
        available = bool(real_account.get("lighter_account_data_available"))
        equity = _number(real_account.get("lighter_account_equity_usd")) if available else 0.0
        return {
            "available": available,
            "base_balance": None,
            "equity": equity,
            "collateral": _number(real_account.get("lighter_collateral_usd")) if available else 0.0,
            "free_margin": _number(real_account.get("lighter_free_margin_usd")) if available else 0.0,
            "margin_used": _number(real_account.get("lighter_margin_used_usd")) if available else 0.0,
            "unrealized_pnl": _number(real_account.get("lighter_unrealized_pnl_usd")) if available else 0.0,
            "position_notional": _number(real_account.get("lighter_position_notional_usd")) if available else 0.0,
        }
    floating_pnl = _number(active_trade.get("floating_pnl_usd")) if active_trade else 0.0
    equity = round(simulation_balance + floating_pnl, 2)
    margin_used = _number(active_trade.get("margin_allocated_usd")) if active_trade else 0.0
    return {
        "available": True,
        "base_balance": simulation_balance,
        "equity": equity,
        "collateral": simulation_balance,
        "free_margin": round(equity - margin_used, 2),
        "margin_used": margin_used,
        "unrealized_pnl": floating_pnl,
        "position_notional": _number(active_trade.get("notional_usd")) if active_trade else 0.0,
    }


def _estimated_round_trip_fee(trade: Mapping[str, Any]) -> float:
    entry_notional = _number(trade.get("notional_usd"))
    size = _number(trade.get("size_btc", trade.get("size")))
    exit_notional = size * _number(trade.get("exit_px", trade.get("exit_price")))
    if entry_notional <= 0:
        entry_notional = size * _number(trade.get("entry_px", trade.get("entry_price")))
    return (entry_notional + exit_notional) * (COMPARATOR_ROUND_TRIP_FEE_RATE / 2)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
