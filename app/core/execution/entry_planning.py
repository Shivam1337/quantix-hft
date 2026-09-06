"""Signal-time L2 plans shared by the real and simulated entry paths."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.core.execution.economics import ArrivalTimeOrder, calculate_arrival_time_executable_order
from app.core.execution.liquidity import ExecutableOrder


@dataclass(frozen=True)
class SignalEntryPlan:
    executable: ExecutableOrder
    arrival: Optional[ArrivalTimeOrder]

    @property
    def meets_requirements(self) -> bool:
        return self.executable.meets_minimums and (self.arrival is None or self.arrival.meets_economics)


def calculate_signal_entry_plan(
    *,
    side: str,
    is_real: bool,
    bids: Iterable[Any],
    asks: Iterable[Any],
    target_price: float,
    target_exit_buffer_usd: float,
    minimum_net_profit_usd: float,
    latency_buffer_usd: float,
    notional_cap_usd: float,
    max_levels: int,
    liquidity_participation: float,
    slippage_buffer_usd: float,
) -> SignalEntryPlan:
    """Build a latency-aware real plan or an instantaneous L2 paper control."""
    buffer = latency_buffer_usd if is_real else 0.0
    arrival = calculate_arrival_time_executable_order(
        side=side, bids=bids, asks=asks, target_price=target_price,
        target_exit_buffer_usd=target_exit_buffer_usd,
        minimum_net_profit_usd=minimum_net_profit_usd,
        estimated_cost_usd=0.0, latency_buffer_usd=buffer,
        notional_cap_usd=notional_cap_usd, max_levels=max_levels,
        liquidity_participation=liquidity_participation,
        slippage_buffer_usd=buffer,
    )
    return SignalEntryPlan(arrival.executable, arrival)
