"""Compact, bounded payloads for the live dashboard control plane.

The REST market-state endpoint intentionally remains comprehensive for API
inspection.  The browser dashboard, however, only needs a small, predictable
set of values at high frequency.  These builders keep the SSE control plane
cheap without changing market-data collection or persisted chart history.
"""
from __future__ import annotations

import copy
import time
from typing import TYPE_CHECKING, Any, Dict, Iterable

from app.core.settings_manager import settings_manager

if TYPE_CHECKING:
    from app.core.state_manager import StateManager


DASHBOARD_TICK_INTERVAL_SECONDS = 0.25
DASHBOARD_DETAIL_INTERVAL_SECONDS = 1.0
DASHBOARD_CHART_POINTS = 120


def _number(value: Any) -> float:
    """Return a JSON-safe numeric display value without exposing book depth."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _top_size(levels: Any) -> float:
    if not isinstance(levels, list) or not levels or not isinstance(levels[0], (list, tuple)):
        return 0.0
    return _number(levels[0][1] if len(levels[0]) > 1 else 0.0)


def _quote(state: Dict[str, Any], *, include_lag: bool = False) -> Dict[str, Any]:
    quote = {
        "mid_price": state.get("mid_price", 0.0),
        "best_bid": state.get("best_bid", 0.0),
        "best_ask": state.get("best_ask", 0.0),
        "spread": state.get("spread", 0.0),
        "status": state.get("status", "WAITING"),
        "top_bid_size": _top_size(state.get("bids")),
        "top_ask_size": _top_size(state.get("asks")),
    }
    if include_lag:
        quote.update(
            {
                "lag_vs_leader": state.get("lag_vs_leader", 0.0),
                "lag_bps": state.get("lag_bps", 0.0),
            }
        )
    return quote


def _market_cards(manager: "StateManager") -> Dict[str, Dict[str, Any]]:
    return {
        "binance": _quote(manager.binance),
        "bybit": _quote(manager.bybit),
        "okx": _quote(manager.okx),
        "hyperliquid": _quote(manager.hl),
        "lighter": _quote(manager.lighter, include_lag=True),
        "polymarket": _quote(manager.poly, include_lag=True),
    }


def _system_tick(manager: "StateManager") -> Dict[str, Any]:
    health = manager.get_health()
    return {
        "status": health["status"],
        "streaming_feeds": health["streaming_feeds"],
        "total_feeds": health["total_feeds"],
        "tick_rate_hz": health["tick_rate_hz"],
        "uptime_seconds": health["uptime_seconds"],
        "uptime_formatted": health["uptime_formatted"],
    }


def build_dashboard_tick(manager: "StateManager") -> Dict[str, Any]:
    """Build the fast path used four times per second by an SSE client."""
    analysis = manager.lead_lag_analyzer.get_latest()
    engine = manager.sniper_engine
    active_position = copy.deepcopy(engine.active_trade)
    if isinstance(active_position, dict):
        active_position.pop("execution_comparison", None)
    return {
        "updated_at": manager.last_recalculated_at,
        "market": _market_cards(manager),
        "dynamic_leader": analysis["dynamic_leader"],
        "consensus_status": analysis["consensus_status"],
        "consensus_agreement": analysis["consensus_agreement"],
        "trade_decision": copy.deepcopy(engine.current_decision),
        "active_position": active_position,
        "trading_enabled": settings_manager.trading_enabled,
        "system": _system_tick(manager),
    }


def _chart_payload(points: Iterable[Dict[str, Any]], backend: str) -> Dict[str, Any]:
    visible = list(points)[-DASHBOARD_CHART_POINTS:]
    return {
        "timestamps": [point.get("time") for point in visible],
        "binance_series": [point.get("binance") for point in visible],
        "bybit_series": [point.get("bybit") for point in visible],
        "okx_series": [point.get("okx") for point in visible],
        "hl_series": [point.get("hl") for point in visible],
        "lighter_series": [point.get("lighter") for point in visible],
        "poly_series": [point.get("poly") for point in visible],
        "lighter_lag_series": [point.get("l_lag") for point in visible],
        "sample_count": len(visible),
        "max_points": DASHBOARD_CHART_POINTS,
        "sample_interval_ms": 250,
        "persistence": backend,
    }


def build_dashboard_detail(manager: "StateManager") -> Dict[str, Any]:
    """Build the one-Hz chart, table, and host-health payload."""
    persistence = manager.get_persistence_status()
    engine = manager.sniper_engine
    comparisons = engine.get_execution_comparisons() if hasattr(engine, "get_execution_comparisons") else []
    return {
        "system": {
            "resources": manager.get_resource_usage(),
            "persistence": persistence,
        },
        "provider_insights": manager.get_provider_insights(now_monotonic_ns=time.monotonic_ns()),
        "chart": _chart_payload(manager.price_history, persistence.get("backend", "database")),
        "trading_performance": copy.deepcopy(engine.get_performance()),
        "recent_trades": copy.deepcopy(list(engine.closed_trades)[:10]),
        "execution_comparisons": copy.deepcopy(comparisons[:10]),
        "recent_repricing_events": manager.lead_lag_analyzer.get_repricing_events()[:10],
    }


def build_dashboard_snapshot(manager: "StateManager") -> Dict[str, Any]:
    """Build the bounded initial state for a newly connected dashboard."""
    snapshot = build_dashboard_tick(manager)
    snapshot.update(build_dashboard_detail(manager))
    return snapshot
