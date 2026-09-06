"""Rolling latency evidence and entry gates for asynchronous IOC execution."""
from __future__ import annotations

import collections
import math
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class ArrivalDecision:
    allowed: bool
    reason: Optional[str]
    arrival_budget_ms: float
    quote_age_ms: Optional[float]
    projected_net_profit_usd: float


class ExecutionLatencyGuard:
    """Reject entries that cannot survive measured API and quote conditions."""

    def __init__(
        self,
        *,
        minimum_arrival_ms: float = 300.0,
        maximum_arrival_ms: float = 1_500.0,
        maximum_book_age_ms: float = 250.0,
        slippage_cancel_limit: int = 3,
        circuit_cooldown_seconds: float = 30.0,
        sample_limit: int = 128,
    ) -> None:
        self.minimum_arrival_ms = max(0.0, float(minimum_arrival_ms))
        self.maximum_arrival_ms = max(self.minimum_arrival_ms, float(maximum_arrival_ms))
        self.maximum_book_age_ms = max(0.0, float(maximum_book_age_ms))
        self.slippage_cancel_limit = max(1, int(slippage_cancel_limit))
        self.circuit_cooldown_seconds = max(0.0, float(circuit_cooldown_seconds))
        self._ack_latencies_ms: collections.deque[float] = collections.deque(maxlen=max(1, int(sample_limit)))
        self._adverse_quote_moves: collections.deque[float] = collections.deque(maxlen=max(1, int(sample_limit)))
        self._slippage_cancel_streak = 0
        self._circuit_opened_at: Optional[float] = None
        self._circuit_signal_key: Optional[str] = None
        self._last_signal_key: Optional[str] = None

    @property
    def arrival_budget_ms(self) -> float:
        return max(self.minimum_arrival_ms, _percentile(self._ack_latencies_ms, 0.95))

    @property
    def adverse_quote_buffer_usd(self) -> float:
        return _percentile(self._adverse_quote_moves, 0.95)

    @property
    def circuit_open(self) -> bool:
        return self._circuit_opened_at is not None

    def record_acknowledgement(
        self,
        *,
        side: str,
        submit_quote: Any,
        acknowledged_quote: Any,
        submit_to_ack_ms: Any,
    ) -> None:
        latency = _number(submit_to_ack_ms)
        if latency is not None:
            self._ack_latencies_ms.append(max(0.0, latency))
        submitted = _number(submit_quote)
        acknowledged = _number(acknowledged_quote)
        if submitted is None or acknowledged is None:
            return
        adverse_move = acknowledged - submitted if str(side).upper() == "LONG" else submitted - acknowledged
        self._adverse_quote_moves.append(max(0.0, adverse_move))

    def record_terminal(self, status: Any, *, now: Optional[float] = None) -> None:
        normalized = str(status or "").strip().lower()
        if normalized == "canceled-too-much-slippage":
            self._slippage_cancel_streak += 1
            if self._slippage_cancel_streak >= self.slippage_cancel_limit:
                self._circuit_opened_at = time.time() if now is None else float(now)
                self._circuit_signal_key = self._last_signal_key
            return
        self._slippage_cancel_streak = 0

    def check_entry(
        self,
        *,
        signal_key: str,
        quote_age_ms: Any,
        projected_net_profit_usd: Any,
        minimum_net_profit_usd: Any,
        now: Optional[float] = None,
    ) -> ArrivalDecision:
        current_time = time.time() if now is None else float(now)
        age = _number(quote_age_ms)
        projected = _number(projected_net_profit_usd) or 0.0
        minimum = _number(minimum_net_profit_usd) or 0.0
        if self._circuit_opened_at is not None:
            elapsed = max(0.0, current_time - self._circuit_opened_at)
            if elapsed < self.circuit_cooldown_seconds:
                return self._deny("SLIPPAGE_CIRCUIT_OPEN", age, projected)
            if signal_key == self._circuit_signal_key:
                return self._deny("SLIPPAGE_CIRCUIT_REQUIRES_FRESH_SIGNAL", age, projected)
            self._circuit_opened_at = None
            self._circuit_signal_key = None
            self._slippage_cancel_streak = 0
        if self.arrival_budget_ms > self.maximum_arrival_ms:
            return self._deny("ARRIVAL_LATENCY_EXCESSIVE", age, projected)
        if age is None or age > self.maximum_book_age_ms:
            return self._deny("STALE_EXECUTION_BOOK", age, projected)
        if projected < minimum:
            return self._deny("INSUFFICIENT_ARRIVAL_ECONOMICS", age, projected)
        return ArrivalDecision(True, None, self.arrival_budget_ms, age, projected)

    def note_signal(self, signal_key: str) -> None:
        self._last_signal_key = signal_key
        if self._circuit_opened_at is not None:
            return

    def hydrate_attempts(self, attempts: Iterable[Mapping[str, Any]]) -> None:
        """Restore measurement samples, without reopening a stale failure circuit."""
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            latencies = attempt.get("latencies_ms")
            latency = latencies.get("submit_to_ack") if isinstance(latencies, Mapping) else None
            order = attempt.get("order")
            book = order.get("lighter_book") if isinstance(order, Mapping) else None
            acknowledgement = order.get("acknowledgement_book") if isinstance(order, Mapping) else None
            side = attempt.get("side")
            self.record_acknowledgement(
                side=str(side or ""),
                submit_quote=_quote(book, side),
                acknowledged_quote=_quote(acknowledgement, side),
                submit_to_ack_ms=latency,
            )

    def _deny(self, reason: str, age: Optional[float], projected: float) -> ArrivalDecision:
        return ArrivalDecision(False, reason, self.arrival_budget_ms, age, projected)


def _quote(book: Any, side: Any) -> Optional[float]:
    if not isinstance(book, Mapping):
        return None
    field = "best_ask" if str(side).upper() == "LONG" else "best_bid"
    return _number(book.get(field))


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        return 0.0
    position = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[position]


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
