"""Low-volume, per-order L2 and lifecycle telemetry for live execution."""
from __future__ import annotations

import collections
import copy
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional


MAX_EXECUTION_ATTEMPTS_HISTORY = 200
BookSnapshotProvider = Callable[[], Mapping[str, Any]]
ExecutionAttemptSink = Callable[[Dict[str, Any]], None]


def capture_lighter_book(
    state: Optional[Mapping[str, Any]],
    *,
    captured_epoch: Optional[float] = None,
    captured_monotonic_ns: Optional[int] = None,
) -> Dict[str, Any]:
    """Capture only the L2 evidence relevant to one order, never raw feed traffic."""
    source = state if isinstance(state, Mapping) else {}
    now_epoch = time.time() if captured_epoch is None else float(captured_epoch)
    now_monotonic = time.monotonic_ns() if captured_monotonic_ns is None else int(captured_monotonic_ns)
    received_monotonic = _integer(source.get("last_update_monotonic_ns"))
    age_ms = (
        round(max(0, now_monotonic - received_monotonic) / 1_000_000, 3)
        if received_monotonic is not None and received_monotonic > 0
        else None
    )
    return {
        "captured_at_utc": _utc(now_epoch),
        "book_received_at_utc": source.get("last_update_utc"),
        "book_age_ms": age_ms,
        "exchange_timestamp_ms": _integer(source.get("exchange_timestamp_ms")),
        "source_sequence": source.get("source_sequence"),
        "best_bid": _number(source.get("best_bid")),
        "best_ask": _number(source.get("best_ask")),
        "spread": _number(source.get("spread")),
        "top_bids": _levels(source.get("bids")),
        "top_asks": _levels(source.get("asks")),
    }


class ExecutionTelemetryMixin:
    """Keeps execution diagnostics bounded, serializable, and persistence-ready."""

    def _init_execution_telemetry(self) -> None:
        self.execution_attempts = collections.deque(maxlen=MAX_EXECUTION_ATTEMPTS_HISTORY)
        self._execution_book_snapshot_provider: Optional[BookSnapshotProvider] = None
        self._execution_attempt_sink: Optional[ExecutionAttemptSink] = None

    def configure_execution_telemetry(
        self,
        *,
        book_snapshot_provider: Optional[BookSnapshotProvider] = None,
        attempt_sink: Optional[ExecutionAttemptSink] = None,
    ) -> None:
        self._execution_book_snapshot_provider = book_snapshot_provider
        self._execution_attempt_sink = attempt_sink

    def hydrate_execution_attempts(self, attempts: Any) -> None:
        self.execution_attempts.clear()
        if isinstance(attempts, list):
            # Persistence returns newest-first.  Bound before extending a deque,
            # otherwise a large restore would discard the newest evidence.
            self.execution_attempts.extend(
                copy.deepcopy(item)
                for item in attempts[:MAX_EXECUTION_ATTEMPTS_HISTORY]
                if isinstance(item, dict)
            )

    def get_execution_attempts(self) -> list[Dict[str, Any]]:
        return copy.deepcopy(list(self.execution_attempts))

    def _begin_execution_telemetry(self, trade: Dict[str, Any], lighter_state: Mapping[str, Any]) -> None:
        signal_at = _number(trade.get("signal_ts")) or time.time()
        trade["execution_telemetry"] = {
            "schema_version": 1,
            "signal": {
                "detected_at_utc": _utc(signal_at),
                "lighter_book": capture_lighter_book(lighter_state, captured_epoch=signal_at),
                "side": trade.get("side"),
                "planned_size_btc": _number(trade.get("planned_size_btc", trade.get("size_btc"))),
                "ioc_limit_price": _number(trade.get("execution_price_limit")),
                "planned_vwap_price": _number(trade.get("planned_entry_px", trade.get("entry_px"))),
                "book_levels_used": _integer(trade.get("book_levels_used")),
            },
            "orders": {},
        }

    def _capture_order_submission(self, trade: Dict[str, Any], phase: str, submitted_at: float) -> None:
        state: Optional[Mapping[str, Any]] = None
        capture_error = None
        if self._execution_book_snapshot_provider is not None:
            try:
                state = self._execution_book_snapshot_provider()
            except Exception as exc:  # Telemetry must never interrupt live risk handling.
                capture_error = str(exc)
        order = self._order_section(trade, phase)
        order.update({
            "submitted_at_utc": _utc(submitted_at),
            "side": trade.get("side"),
            "requested_size_btc": _number(trade.get("size_btc")),
            "ioc_limit_price": _number(
                trade.get("execution_price_limit") if phase == "ENTRY" else trade.get("exit_requested_px")
            ),
            "reduce_only": phase == "EXIT",
            "lighter_book": capture_lighter_book(state, captured_epoch=submitted_at),
        })
        if capture_error:
            order["book_capture_error"] = capture_error

    def _record_order_acknowledgement(
        self,
        trade: Dict[str, Any],
        phase: str,
        acknowledged_at: float,
        tx_hash: Optional[str],
    ) -> None:
        self._order_section(trade, phase).update({
            "acknowledged_at_utc": _utc(acknowledged_at),
            "transaction_hash": tx_hash,
        })

    def _record_execution_terminal(
        self,
        trade: Dict[str, Any],
        *,
        phase: str,
        result: str,
        observed_at: Optional[float] = None,
        outcome: Any = None,
        error: Optional[str] = None,
    ) -> None:
        observed = time.time() if observed_at is None else float(observed_at)
        order = self._order_section(trade, phase)
        terminal = {
            "result": result,
            "observed_at_utc": _utc(observed),
            "exchange_status": getattr(outcome, "status", None),
            "exchange_timestamp": getattr(outcome, "exchange_timestamp", None),
            "filled_size_btc": _number(getattr(outcome, "filled_size_btc", None)),
            "filled_quote_usd": _number(getattr(outcome, "filled_quote_usd", None)),
            "average_fill_price": _number(getattr(outcome, "average_fill_price", None)),
            "error": error,
        }
        order["terminal"] = terminal
        prefix = phase.lower()
        _record_latency(trade, f"{prefix}_ack_to_terminal_observed", trade.get(f"{prefix}_ack_ts"), observed)
        signal_key = "signal_ts" if phase == "ENTRY" else "exit_signal_ts"
        _record_latency(trade, f"{prefix}_signal_to_terminal_observed", trade.get(signal_key), observed)
        submitted = _number(trade.get(f"{prefix}_submit_ts"))
        suffix = int(submitted * 1_000_000) if submitted is not None else int(observed * 1_000_000)
        tx_hash = trade.get("tx_hash") if phase == "ENTRY" else trade.get("exit_tx_hash")
        attempt = {
            "attempt_id": f"{trade.get('id', 'unknown')}:{phase}:{suffix}",
            "recorded_at_utc": _utc(observed),
            "trade_id": trade.get("id"),
            "client_order_index": _client_order_index(trade, phase),
            "phase": phase,
            "mode": "REAL",
            "side": trade.get("side"),
            "result": result,
            "transaction_hash": tx_hash,
            "signal": copy.deepcopy(trade.get("execution_telemetry", {}).get("signal")),
            "order": copy.deepcopy(order),
            "latencies_ms": copy.deepcopy(trade.get("latencies_ms", {})),
        }
        self.execution_attempts.appendleft(attempt)
        if self._execution_attempt_sink is not None:
            self._execution_attempt_sink(copy.deepcopy(attempt))

    @staticmethod
    def _order_section(trade: Dict[str, Any], phase: str) -> Dict[str, Any]:
        telemetry = trade.setdefault("execution_telemetry", {"schema_version": 1, "signal": None, "orders": {}})
        return telemetry.setdefault("orders", {}).setdefault(phase.lower(), {})


def _client_order_index(trade: Mapping[str, Any], phase: str) -> Optional[int]:
    trade_id = _integer(trade.get("id"))
    if trade_id is None:
        return None
    return trade_id if phase == "ENTRY" else trade_id + 10_000


def _record_latency(trade: Dict[str, Any], name: str, start: Any, end: float) -> None:
    start_time = _number(start)
    if start_time is not None:
        trade.setdefault("latencies_ms", {})[name] = round(max(0.0, end - start_time) * 1000, 2)


def _levels(raw_levels: Any) -> list[Dict[str, Optional[float]]]:
    if not isinstance(raw_levels, (list, tuple)):
        return []
    result = []
    for level in raw_levels[:3]:
        if isinstance(level, Mapping):
            price, size = level.get("price"), level.get("size")
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price, size = level[0], level[1]
        else:
            continue
        result.append({"price": _number(price), "size": _number(size)})
    return result


def _number(value: Any) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and value not in {float("inf"), float("-inf")} else None


def _integer(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
