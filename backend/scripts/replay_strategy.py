"""Replay a deterministic confirmed-funding path with the paper cost model."""

import json
from dataclasses import replace
from datetime import datetime, timezone

from app.domain.execution import ExecutionManager, PaperExecutionConfig
from app.domain.risk import RiskConfig, RiskEngine
from app.domain.types import OpportunityData


def opportunity(mark: float, rate: float = 0.0003) -> OpportunityData:
    return OpportunityData(
        id="LINK-PERP:hyperliquid:lighter",
        symbol="LINK-PERP",
        long_venue="hyperliquid",
        short_venue="lighter",
        long_funding_rate=0.0,
        short_funding_rate=rate,
        gross_hourly_rate=rate,
        net_hourly_rate=rate,
        net_apr_pct=rate * 24 * 365 * 100,
        basis_bps=0.0,
        capacity_usd=100_000,
        fee_bps=3.0,
        entry_fee_bps=1.5,
        exit_fee_bps=1.5,
        round_trip_fee_bps=3.0,
        fee_breakeven_hours=1.0,
        long_order_type="post_only_limit",
        short_order_type="market",
        long_mark_price=mark,
        short_mark_price=mark,
        min_open_interest=1_000_000,
        observed_at=datetime.now(timezone.utc),
        long_bid_price=mark - 0.01,
        long_ask_price=mark + 0.01,
        short_bid_price=mark - 0.01,
        short_ask_price=mark + 0.01,
    )


def replay() -> dict:
    costs = PaperExecutionConfig(slippage_bps=1.0, post_only_fill_ratio=0.90)
    execution = ExecutionManager(paper_config=costs)
    risk = RiskEngine(RiskConfig(basis_threshold_bps=200, negative_hours_to_unwind=2))
    entry = opportunity(100.0)
    opened = execution.open_pair(entry, 1_000)
    matched = opened.matched_size_usd
    funding_rates = [0.0003] * 8 + [-0.0001, -0.0001] + [0.0003] * 6
    negative_hours = 0
    funding_pnl = 0.0
    exit_reason = "replay horizon"
    exit_hour = len(funding_rates)
    close_mark = entry.long_mark_price
    for hour, rate in enumerate(funding_rates, start=1):
        funding_pnl += rate * matched
        negative_hours = negative_hours + 1 if rate < 0 else 0
        close_mark = 100.0 + hour * 0.01
        decision = risk.evaluate(rate * 24 * 365 * 100, 0.0, negative_hours)
        if decision.should_unwind:
            exit_reason = decision.reason or "risk guard"
            exit_hour = hour
            break
    closing = replace(entry, long_mark_price=close_mark, short_mark_price=close_mark)
    remaining = matched
    close_attempts = 0
    close_temporary_exposure = 0.0
    basis_pnl = 0.0
    close_fees = 0.0
    while remaining > 0.01 and close_attempts < 12:
        closed = execution.close_pair(closing, remaining)
        filled = closed.matched_size_usd
        if filled <= 0:
            break
        basis_pnl += (
            (closed.legs[0].price - opened.legs[0].price) / opened.legs[0].price * filled
            + (opened.legs[1].price - closed.legs[1].price)
            / opened.legs[1].price
            * filled
        )
        close_fees += sum(leg.fee_usd for leg in closed.legs)
        close_temporary_exposure += closed.temporary_exposure_usd
        remaining -= filled
        close_attempts += 1
    fees = sum(leg.fee_usd for leg in opened.legs) + close_fees
    gross_pnl = funding_pnl + basis_pnl
    return {
        "thresholds": {"basis_bps": 200, "negative_hours": 2},
        "cost_model": {
            "slippage_bps": costs.slippage_bps,
            "post_only_fill_ratio": costs.post_only_fill_ratio,
        },
        "requested_leg_notional_usd": opened.requested_size_usd,
        "matched_leg_notional_usd": matched,
        "temporary_exposure_usd": opened.temporary_exposure_usd,
        "exit_attempts": close_attempts,
        "exit_matched_leg_notional_usd": matched - remaining,
        "exit_remaining_leg_notional_usd": remaining,
        "exit_temporary_exposure_usd": close_temporary_exposure,
        "exit_hour": exit_hour,
        "exit_reason": exit_reason,
        "gross_funding_pnl_usd": funding_pnl,
        "gross_basis_pnl_usd": basis_pnl,
        "gross_pnl_usd": gross_pnl,
        "paid_fees_usd": fees,
        "net_pnl_usd": gross_pnl - fees,
        "thresholds_changed": False,
    }


if __name__ == "__main__":
    print(json.dumps(replay(), sort_keys=True))
