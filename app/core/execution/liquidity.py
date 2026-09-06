"""Pure, bounded sizing against the visible Lighter order book."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
from typing import Any, Iterable, Optional, Tuple


BTC_SIZE_DECIMALS = 5
MIN_EXECUTABLE_SIZE_BTC = 0.00010
MIN_EXECUTABLE_NOTIONAL_USD = 10.00

_BTC_SIZE_STEP = Decimal(1).scaleb(-BTC_SIZE_DECIMALS)
_PRICE_TICK = Decimal("0.1")


@dataclass(frozen=True)
class ExecutableOrder:
    """The quantity that can be immediately filled through a bounded book ladder."""

    side: str
    limit_price: float
    profitability_limit_price: float
    requested_notional_usd: float
    visible_size_btc: float
    visible_notional_usd: float
    size_btc: float
    notional_usd: float
    worst_case_notional_usd: float
    limit_notional_usd: float
    vwap_price: float
    levels_used: int
    ladder_limit_price: float = 0.0

    @property
    def meets_minimums(self) -> bool:
        """A valid order must clear both the base-size and strict $10 USDC floors."""
        return (
            self.size_btc >= MIN_EXECUTABLE_SIZE_BTC
            and self.limit_notional_usd > MIN_EXECUTABLE_NOTIONAL_USD
        )


def calculate_profitable_price_limit(
    *,
    side: str,
    target_price: float,
    target_exit_buffer_usd: float,
    minimum_expected_profit_usd: float,
) -> float:
    """Return a tick-aligned entry bound that preserves positive expected PnL.

    The target exit is intentionally more conservative than the leader target: a
    long exits at ``target - target_exit_buffer_usd`` and a short exits at
    ``target + target_exit_buffer_usd``. Each ladder level must still retain the
    requested positive expected profit beyond that exit price.
    """
    direction = _normalise_side(side)
    target = _positive_decimal(target_price)
    exit_buffer = _positive_or_zero_decimal(target_exit_buffer_usd)
    profit = _positive_decimal(minimum_expected_profit_usd)
    if target is None or exit_buffer is None or profit is None:
        return 0.0

    raw_limit = (
        target - exit_buffer - profit
        if direction == "LONG"
        else target + exit_buffer + profit
    )
    if raw_limit <= 0:
        return 0.0
    rounding = ROUND_FLOOR if direction == "LONG" else ROUND_CEILING
    return float(_round_to_price_tick(raw_limit, rounding))


def calculate_executable_order(
    *,
    side: str,
    bids: Iterable[Any],
    asks: Iterable[Any],
    limit_price: float,
    notional_cap_usd: float,
    max_levels: int = 1,
    liquidity_participation: float = 1.0,
    slippage_buffer_usd: float = 0.0,
) -> ExecutableOrder:
    """Cap an IOC order to a fraction of displayed depth and its notional ceiling.

    LONG/BUY consumes asks at or below ``limit_price``. SHORT/SELL consumes bids
    at or above it. ``max_levels`` bounds the ladder, ``liquidity_participation``
    caps each level's size, and ``slippage_buffer_usd`` extends the IOC limit beyond
    the ladder tick to absorb in-flight quote moves up to ``profitability_limit``.
    """
    direction = _normalise_side(side)
    profitability_limit = _positive_decimal(limit_price)
    cap = _positive_decimal(notional_cap_usd)
    participation = _normalise_liquidity_participation(liquidity_participation)
    levels = _eligible_levels(direction, bids, asks, profitability_limit)[:_normalise_max_levels(max_levels)]

    visible_size = sum((size for _, size in levels), Decimal())
    visible_notional = sum((price * size for price, size in levels), Decimal())
    if profitability_limit is None or cap is None or not levels:
        return _empty_order(direction, profitability_limit, cap, visible_size, visible_notional)

    executable_size = Decimal()
    executable_notional = Decimal()
    worst_observed_price = Decimal()
    order_limit = Decimal()
    participating_cap = cap * participation
    levels_used = 0
    for price, visible_at_level in levels:
        candidate_worst_price = max(worst_observed_price, price)
        maximum_safe_size = _floor_btc_size(participating_cap / candidate_worst_price)
        remaining_safe_size = maximum_safe_size - executable_size
        if remaining_safe_size <= 0:
            break
        participating_level_size = _floor_btc_size(visible_at_level * participation)
        allowed_size = min(participating_level_size, remaining_safe_size)
        size = _floor_btc_size(allowed_size)
        if size <= 0:
            continue
        executable_size += size
        executable_notional += price * size
        worst_observed_price = candidate_worst_price
        order_limit = price
        levels_used += 1

    buffer = _positive_or_zero_decimal(slippage_buffer_usd) or Decimal()
    if order_limit and buffer > 0 and profitability_limit is not None:
        if direction == "LONG":
            raw_limit = min(profitability_limit, order_limit + buffer)
            execution_limit = _round_to_price_tick(raw_limit, ROUND_FLOOR)
        else:
            raw_limit = max(profitability_limit, order_limit - buffer)
            execution_limit = _round_to_price_tick(raw_limit, ROUND_CEILING)
    else:
        execution_limit = order_limit

    limit_notional = executable_size * order_limit if order_limit else Decimal()
    worst_price = max(worst_observed_price, execution_limit) if direction == "LONG" and execution_limit else worst_observed_price
    worst_case_notional = executable_size * worst_price if executable_size else Decimal()
    return ExecutableOrder(
        side=direction,
        limit_price=float(execution_limit),
        profitability_limit_price=float(profitability_limit),
        requested_notional_usd=float(cap),
        visible_size_btc=float(_floor_btc_size(visible_size)),
        visible_notional_usd=float(visible_notional),
        size_btc=float(_floor_btc_size(executable_size)),
        notional_usd=float(executable_notional),
        worst_case_notional_usd=float(worst_case_notional),
        limit_notional_usd=float(limit_notional),
        vwap_price=round(float(executable_notional / executable_size), 8) if executable_size else 0.0,
        levels_used=levels_used,
        ladder_limit_price=float(order_limit),
    )


def _normalise_side(side: str) -> str:
    normalised = str(side).upper()
    if normalised in {"LONG", "BUY"}:
        return "LONG"
    if normalised in {"SHORT", "SELL"}:
        return "SHORT"
    raise ValueError(f"Unsupported execution side: {side!r}")


def _eligible_levels(
    side: str,
    bids: Iterable[Any],
    asks: Iterable[Any],
    limit: Optional[Decimal],
) -> list[Tuple[Decimal, Decimal]]:
    if limit is None:
        return []
    raw_levels = asks if side == "LONG" else bids
    parsed = [level for item in raw_levels or [] if (level := _parse_level(item)) is not None]
    if side == "LONG":
        return sorted((level for level in parsed if level[0] <= limit), key=lambda level: level[0])
    return sorted((level for level in parsed if level[0] >= limit), key=lambda level: level[0], reverse=True)


def _parse_level(level: Any) -> Optional[Tuple[Decimal, Decimal]]:
    if isinstance(level, dict):
        raw_price, raw_size = level.get("price"), level.get("size")
    elif isinstance(level, (list, tuple)) and len(level) >= 2:
        raw_price, raw_size = level[0], level[1]
    else:
        raw_price, raw_size = getattr(level, "price", None), getattr(level, "size", None)
    price = _positive_decimal(raw_price)
    size = _positive_decimal(raw_size)
    return (price, size) if price is not None and size is not None else None


def _positive_decimal(value: Any) -> Optional[Decimal]:
    try:
        dec = Decimal(str(value))
        return dec if dec.is_finite() and dec > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _positive_or_zero_decimal(value: Any) -> Optional[Decimal]:
    try:
        dec = Decimal(str(value))
        return dec if dec.is_finite() and dec >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _floor_btc_size(value: Decimal) -> Decimal:
    return value.quantize(_BTC_SIZE_STEP, rounding=ROUND_DOWN)


def _round_to_price_tick(value: Decimal, rounding: str) -> Decimal:
    return (value / _PRICE_TICK).to_integral_value(rounding=rounding) * _PRICE_TICK


def _normalise_max_levels(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _normalise_liquidity_participation(value: Any) -> Decimal:
    try:
        part = Decimal(str(value))
        return min(Decimal(1), max(Decimal(), part)) if part.is_finite() else Decimal()
    except (InvalidOperation, TypeError, ValueError):
        return Decimal()


def _empty_order(
    side: str,
    profitability_limit: Optional[Decimal],
    cap: Optional[Decimal],
    visible_size: Decimal,
    visible_notional: Decimal,
) -> ExecutableOrder:
    return ExecutableOrder(
        side=side, limit_price=0.0,
        profitability_limit_price=float(profitability_limit) if profitability_limit is not None else 0.0,
        requested_notional_usd=float(cap) if cap is not None else 0.0,
        visible_size_btc=float(_floor_btc_size(visible_size)),
        visible_notional_usd=float(visible_notional),
        size_btc=0.0, notional_usd=0.0, worst_case_notional_usd=0.0,
        limit_notional_usd=0.0, vwap_price=0.0, levels_used=0,
    )
