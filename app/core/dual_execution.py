"""Matched paper controls for opt-in DUAL live execution.

DUAL mode sends the normal live IOC order, but records the same signal as an
instantaneous L2-ladder paper fill.  The comparison deliberately uses the
same signal and exit trigger so a PnL delta describes execution quality rather
than two different strategy decisions.
"""
from __future__ import annotations

import collections
import copy
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from app.config import MAX_CLOSED_TRADES_HISTORY


class DualExecutionMixin:
    """Keep bounded, JSON-safe real-versus-simulated execution comparisons."""

    def _init_dual_execution(self) -> None:
        self.execution_comparisons: collections.deque[Dict[str, Any]] = collections.deque(
            maxlen=MAX_CLOSED_TRADES_HISTORY
        )

    @staticmethod
    def _dual_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dual_number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _begin_dual_comparison(self, trade: Dict[str, Any]) -> None:
        """Create the simulated control at the precise live-order signal."""
        planned_size = self._dual_number(trade.get("planned_size_btc", trade.get("size_btc")))
        planned_entry = self._dual_number(trade.get("planned_entry_px", trade.get("entry_px")))
        comparison = {
            "comparison_id": int(trade["id"]),
            "created_at": self._dual_now(),
            "updated_at": self._dual_now(),
            "status": "LIVE_ENTRY_SUBMITTED",
            "side": trade.get("side"),
            "leader": trade.get("leader_name"),
            "signal_time": trade.get("entry_time"),
            "simulated": {
                "status": "SIMULATED_OPEN",
                "entry_price": planned_entry,
                "size_btc": planned_size,
                "notional_usd": round(planned_size * planned_entry, 2),
                "entry_time": trade.get("entry_time"),
                "fill_model": "L2_LADDER_VWAP_AT_SIGNAL",
            },
            "real": {
                "status": "ENTRY_SUBMITTED",
                "planned_size_btc": planned_size,
                "planned_entry_price": planned_entry,
                "entry_order_status": trade.get("order_status"),
            },
        }
        trade["dual_execution"] = True
        trade["execution_comparison"] = comparison
        self.execution_comparisons.appendleft(comparison)

    @staticmethod
    def _comparison_for_trade(trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        comparison = trade.get("execution_comparison")
        return comparison if isinstance(comparison, dict) else None

    def _touch_comparison(self, comparison: Dict[str, Any], status: str) -> None:
        comparison["status"] = status
        comparison["updated_at"] = self._dual_now()

    def _record_dual_entry_failure(
        self, trade: Dict[str, Any], *, status: str, error: Optional[str] = None
    ) -> None:
        comparison = self._comparison_for_trade(trade)
        if comparison is None:
            return
        comparison["real"].update({"status": status, "error": error})
        comparison["simulated"]["status"] = "SIMULATED_ONLY"
        self._touch_comparison(comparison, status)

    def _record_dual_entry_fill(self, trade: Dict[str, Any]) -> None:
        comparison = self._comparison_for_trade(trade)
        if comparison is None:
            return
        simulated = comparison["simulated"]
        real = comparison["real"]
        planned_size = self._dual_number(simulated.get("size_btc"))
        filled_size = self._dual_number(trade.get("filled_size_btc", trade.get("size_btc")))
        matched_size = min(planned_size, filled_size)
        real_entry = self._dual_number(trade.get("entry_px"))
        simulated_entry = self._dual_number(simulated.get("entry_price"))
        side_multiplier = 1.0 if trade.get("side") == "LONG" else -1.0
        simulated.update({
            "status": "OPEN",
            "matched_size_btc": matched_size,
            "matched_notional_usd": round(matched_size * simulated_entry, 2),
        })
        real.update({
            "status": "PARTIAL" if trade.get("entry_fill_status") == "PARTIAL" else "FILLED",
            "entry_price": real_entry,
            "filled_size_btc": filled_size,
            "matched_size_btc": matched_size,
            "fill_ratio": round(filled_size / planned_size, 4) if planned_size else 0.0,
            "entry_latency_ms": trade.get("latencies_ms", {}).get("signal_to_fill"),
            "entry_order_status": trade.get("order_status"),
        })
        comparison["entry_price_penalty_usd"] = round(
            (real_entry - simulated_entry) * side_multiplier, 4
        )
        self._touch_comparison(comparison, "OPEN")

    def _capture_dual_exit_signal(
        self, trade: Dict[str, Any], exit_price: float, exit_reason: str
    ) -> None:
        """Close the paper control when the identical real exit is requested."""
        comparison = self._comparison_for_trade(trade)
        if comparison is None:
            return
        simulated = comparison["simulated"]
        if simulated.get("status") == "SIMULATED_CLOSED":
            return
        size = self._dual_number(simulated.get("matched_size_btc", simulated.get("size_btc")))
        entry_price = self._dual_number(simulated.get("entry_price"))
        side = trade.get("side")
        pnl = (exit_price - entry_price) * size if side == "LONG" else (entry_price - exit_price) * size
        signal_ts = self._dual_number(trade.get("signal_ts", trade.get("entry_ts")))
        simulated.update({
            "status": "SIMULATED_CLOSED",
            "exit_price": exit_price,
            "net_pnl": round(pnl, 6),
            "hold_sec": round(max(0.0, time.time() - signal_ts), 1),
            "exit_reason": exit_reason,
        })
        comparison["real"].update({"status": "EXIT_SUBMITTED", "exit_requested_price": exit_price})
        self._touch_comparison(comparison, "LIVE_EXIT_PENDING")

    def _record_dual_exit_failure(self, trade: Dict[str, Any], error: Optional[str]) -> None:
        comparison = self._comparison_for_trade(trade)
        if comparison is None:
            return
        comparison["real"].update({"status": "EXIT_SUBMISSION_FAILED", "exit_error": error})
        self._touch_comparison(comparison, "LIVE_EXIT_SUBMISSION_FAILED")

    def _finalize_dual_comparison(self, trade: Dict[str, Any], closed: Dict[str, Any]) -> None:
        comparison = self._comparison_for_trade(trade)
        if comparison is None:
            return
        simulated = comparison["simulated"]
        real = comparison["real"]
        real_exit = self._dual_number(closed.get("exit_px"))
        simulated_exit = self._dual_number(simulated.get("exit_price"))
        side_multiplier = 1.0 if trade.get("side") == "LONG" else -1.0
        real.update({
            "status": "CLOSED",
            "entry_price": closed.get("entry_px"),
            "exit_price": real_exit,
            "net_pnl": closed.get("net_pnl"),
            "hold_sec": closed.get("hold_sec"),
            "exit_latency_ms": trade.get("latencies_ms", {}).get("exit_signal_to_fill"),
            "exit_order_status": closed.get("order_status"),
        })
        if simulated.get("status") == "SIMULATED_CLOSED":
            comparison["exit_price_penalty_usd"] = round(
                (simulated_exit - real_exit) * side_multiplier, 4
            )
            comparison["pnl_delta_usd"] = round(
                self._dual_number(closed.get("net_pnl")) - self._dual_number(simulated.get("net_pnl")), 6
            )
        self._touch_comparison(comparison, "COMPLETE")
        closed["execution_comparison"] = copy.deepcopy(comparison)

    def _record_dual_shutdown(self, trade: Dict[str, Any]) -> None:
        comparison = self._comparison_for_trade(trade)
        if comparison is None:
            return
        comparison["real"]["status"] = "PROCESS_SHUTDOWN"
        self._touch_comparison(comparison, "PROCESS_SHUTDOWN")

    def get_execution_comparisons(self) -> list[Dict[str, Any]]:
        return copy.deepcopy(list(self.execution_comparisons))

    def hydrate_execution_comparisons(self, records: Iterable[Dict[str, Any]]) -> None:
        """Restore only the latest persisted terminal comparison for each signal."""
        restored: Dict[int, Dict[str, Any]] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                comparison_id = int(record.get("comparison_id"))
            except (TypeError, ValueError):
                continue
            restored.setdefault(comparison_id, copy.deepcopy(record))
        self.execution_comparisons.clear()
        self.execution_comparisons.extend(restored.values())
