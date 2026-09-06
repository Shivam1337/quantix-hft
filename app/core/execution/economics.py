"""Arrival-time execution economics expressed in real USD, not price points."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from app.core.execution.liquidity import ExecutableOrder, calculate_executable_order


@dataclass(frozen=True)
class ArrivalTimeOrder:
    """A bounded L2 order together with its conservative net-profit proof."""

    executable: ExecutableOrder
    target_exit_price: float
    required_price_edge_usd: float
    latency_buffer_usd: float
    minimum_net_profit_usd: float
    estimated_cost_usd: float
    projected_net_profit_usd: float

    @property
    def meets_economics(self) -> bool:
        return self.executable.meets_minimums and self.projected_net_profit_usd >= self.minimum_net_profit_usd


def calculate_arrival_time_executable_order(
    *,
    side: str,
    bids: Iterable[Any],
    asks: Iterable[Any],
    target_price: float,
    target_exit_buffer_usd: float,
    minimum_net_profit_usd: float,
    estimated_cost_usd: float,
    latency_buffer_usd: float,
    notional_cap_usd: float,
    max_levels: int,
    liquidity_participation: float,
    slippage_buffer_usd: float,
) -> ArrivalTimeOrder:
    """Use a provisional depth size, then reprice against actual executable size.

    A $1 desired profit is not a $1 BTC price offset.  For a 0.001 BTC
    order, it needs roughly a $1,000 price edge before costs.  The second pass
    makes the limit stricter whenever visible depth reduces the executable size.
    """
    normalized_side = _side(side)
    target_exit = _target_exit(normalized_side, target_price, target_exit_buffer_usd)
    minimum_profit = max(0.0, _number(minimum_net_profit_usd))
    costs = max(0.0, _number(estimated_cost_usd))
    latency = max(0.0, _number(latency_buffer_usd))
    cap = max(0.0, _number(notional_cap_usd))
    provisional_size = cap / target_exit if target_exit > 0 else 0.0
    provisional_edge = _required_edge(provisional_size, minimum_profit, costs, latency)
    initial = calculate_executable_order(
        side=normalized_side,
        bids=bids,
        asks=asks,
        limit_price=_limit_price(normalized_side, target_exit, provisional_edge),
        notional_cap_usd=cap,
        max_levels=max_levels,
        liquidity_participation=liquidity_participation,
        slippage_buffer_usd=max(latency, _number(slippage_buffer_usd)),
    )
    actual_size = initial.size_btc
    required_edge = _required_edge(actual_size, minimum_profit, costs, latency)
    final = calculate_executable_order(
        side=normalized_side,
        bids=bids,
        asks=asks,
        limit_price=_limit_price(normalized_side, target_exit, required_edge),
        notional_cap_usd=cap,
        max_levels=max_levels,
        liquidity_participation=liquidity_participation,
        slippage_buffer_usd=max(latency, _number(slippage_buffer_usd)),
    )
    projected = _projected_net_profit(normalized_side, target_exit, final.limit_price, final.size_btc, costs, latency)
    return ArrivalTimeOrder(
        executable=final,
        target_exit_price=target_exit,
        required_price_edge_usd=required_edge,
        latency_buffer_usd=latency,
        minimum_net_profit_usd=minimum_profit,
        estimated_cost_usd=costs,
        projected_net_profit_usd=projected,
    )


def _side(value: str) -> str:
    normalized = str(value).upper()
    if normalized in {"LONG", "BUY"}:
        return "LONG"
    if normalized in {"SHORT", "SELL"}:
        return "SHORT"
    raise ValueError(f"Unsupported execution side: {value!r}")


def _target_exit(side: str, target: Any, buffer: Any) -> float:
    target_price = max(0.0, _number(target))
    exit_buffer = max(0.0, _number(buffer))
    return target_price - exit_buffer if side == "LONG" else target_price + exit_buffer


def _required_edge(size_btc: float, minimum_profit: float, costs: float, latency: float) -> float:
    return ((minimum_profit + costs) / size_btc + latency) if size_btc > 0 else math.inf


def _limit_price(side: str, target_exit: float, required_edge: float) -> float:
    raw = target_exit - required_edge if side == "LONG" else target_exit + required_edge
    if raw <= 0 or not math.isfinite(raw):
        return 0.0
    rounded = math.floor(raw * 10) / 10 if side == "LONG" else math.ceil(raw * 10) / 10
    return round(rounded, 1)


def _projected_net_profit(
    side: str,
    target_exit: float,
    execution_limit: float,
    size_btc: float,
    costs: float,
    latency: float,
) -> float:
    if size_btc <= 0 or execution_limit <= 0:
        return float("-inf")
    edge = target_exit - execution_limit if side == "LONG" else execution_limit - target_exit
    return round(edge * size_btc - costs - latency * size_btc, 8)


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0
