"""Unit coverage for the bounded dashboard control-plane payloads."""
from __future__ import annotations

import collections
import unittest

from app.api.routes_system import _sse_event
from app.core.dashboard_payload import (
    DASHBOARD_CHART_POINTS,
    DASHBOARD_DETAIL_INTERVAL_SECONDS,
    DASHBOARD_TICK_INTERVAL_SECONDS,
    build_dashboard_detail,
    build_dashboard_snapshot,
    build_dashboard_tick,
)


def _venue(price: float) -> dict:
    return {
        "mid_price": price,
        "best_bid": price - 0.5,
        "best_ask": price + 0.5,
        "spread": 1.0,
        "status": "WS STREAMING",
        "bids": [[str(price - 0.5), "1.25"], [str(price - 1), "2.0"]],
        "asks": [[str(price + 0.5), "2.50"], [str(price + 1), "3.0"]],
        "lag_vs_leader": 3.0,
        "lag_bps": 0.4,
    }


class _Analyzer:
    def get_latest(self):
        return {
            "dynamic_leader": "Binance",
            "consensus_status": "HIGH_CONVICTION",
            "consensus_agreement": "3/4 major venues aligned (UP)",
        }

    def get_repricing_events(self):
        return [{"event_id": "event-1", "timestamp": "12:00:00", "resolved": True}]


class _Engine:
    current_decision = {"stance": "MONITORING", "action": "NONE"}
    active_trade = None
    closed_trades = collections.deque([{"id": 7, "net_pnl": 0.12}], maxlen=20)

    @staticmethod
    def get_performance():
        return {"trading_mode": "SIMULATION", "net_pnl": 0.12, "total_trades": 1}

    @staticmethod
    def get_execution_comparisons():
        return [{"comparison_id": 7, "status": "COMPLETE"}]


class _Manager:
    def __init__(self):
        self.binance = _venue(100.0)
        self.bybit = _venue(100.1)
        self.okx = _venue(100.2)
        self.hl = _venue(100.3)
        self.lighter = _venue(100.4)
        self.poly = _venue(100.5)
        self.last_recalculated_at = "2026-09-06T10:00:00+00:00"
        self.lead_lag_analyzer = _Analyzer()
        self.sniper_engine = _Engine()
        self.price_history = collections.deque(
            [
                {
                    "time": f"2026-09-06T10:00:{index:02d}+00:00",
                    "binance": float(index), "bybit": float(index), "okx": float(index),
                    "hl": float(index), "lighter": float(index), "poly": float(index),
                    "l_lag": float(index) / 10,
                }
                for index in range(DASHBOARD_CHART_POINTS + 25)
            ]
        )

    @staticmethod
    def get_health():
        return {
            "status": "HEALTHY", "streaming_feeds": 6, "total_feeds": 6,
            "tick_rate_hz": 250.0, "uptime_seconds": 9.0, "uptime_formatted": "00:00:09",
        }

    @staticmethod
    def get_resource_usage():
        return {"system_cpu_percent": 10.0, "process_cpu_percent": 5.0}

    @staticmethod
    def get_persistence_status():
        return {"backend": "sqlite", "connected": True, "records_written": 4}

    @staticmethod
    def get_provider_insights(**_kwargs):
        return {"providers": [{"id": "binance", "fresh": True}]}


class DashboardPayloadTests(unittest.TestCase):
    def setUp(self):
        self.manager = _Manager()

    def test_tick_exposes_only_fast_control_values(self):
        tick = build_dashboard_tick(self.manager)

        self.assertNotIn("chart", tick)
        self.assertNotIn("recent_trades", tick)
        self.assertEqual("HIGH_CONVICTION", tick["consensus_status"])
        self.assertEqual(1.25, tick["market"]["binance"]["top_bid_size"])
        self.assertNotIn("bids", tick["market"]["binance"])

    def test_tick_omits_nested_dual_comparison_detail(self):
        self.manager.sniper_engine.active_trade = {
            "id": 7,
            "dual_execution": True,
            "execution_comparison": {"simulated": {"large": "detail"}},
        }

        tick = build_dashboard_tick(self.manager)

        self.assertTrue(tick["active_position"]["dual_execution"])
        self.assertNotIn("execution_comparison", tick["active_position"])

    def test_detail_bounds_chart_history_for_browser_rendering(self):
        detail = build_dashboard_detail(self.manager)
        chart = detail["chart"]

        self.assertEqual(DASHBOARD_CHART_POINTS, chart["sample_count"])
        self.assertEqual(DASHBOARD_CHART_POINTS, len(chart["timestamps"]))
        self.assertEqual(DASHBOARD_CHART_POINTS, len(chart["lighter_lag_series"]))
        self.assertEqual(float(25), chart["binance_series"][0])
        self.assertEqual("sqlite", chart["persistence"])
        self.assertEqual("COMPLETE", detail["execution_comparisons"][0]["status"])

    def test_snapshot_combines_fast_and_detail_payloads(self):
        snapshot = build_dashboard_snapshot(self.manager)

        self.assertIn("market", snapshot)
        self.assertIn("provider_insights", snapshot)
        self.assertIn("trading_performance", snapshot)
        self.assertEqual(1, len(snapshot["recent_trades"]))

    def test_stream_cadences_are_control_plane_safe(self):
        self.assertEqual(0.25, DASHBOARD_TICK_INTERVAL_SECONDS)
        self.assertEqual(1.0, DASHBOARD_DETAIL_INTERVAL_SECONDS)
        self.assertGreater(DASHBOARD_DETAIL_INTERVAL_SECONDS, DASHBOARD_TICK_INTERVAL_SECONDS)

    def test_sse_events_are_named_and_compact(self):
        self.assertEqual('event: tick\ndata:{"value":1}\n\n', _sse_event("tick", {"value": 1}))


if __name__ == "__main__":
    unittest.main()
