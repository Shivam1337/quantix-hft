from dataclasses import replace
from datetime import datetime, timezone

import pytest
from app.domain.execution import ExecutionManager
from app.domain.fees import FeeSchedule
from app.domain.types import OpportunityData


def opportunity(long_venue: str, short_venue: str) -> OpportunityData:
    return OpportunityData(
        id=f"BTC-PERP:{long_venue}:{short_venue}",
        symbol="BTC-PERP",
        long_venue=long_venue,
        short_venue=short_venue,
        long_funding_rate=-0.0001,
        short_funding_rate=0.0005,
        gross_hourly_rate=0.0006,
        net_hourly_rate=0.0005,
        net_apr_pct=438.0,
        basis_bps=0,
        capacity_usd=10_000,
        fee_bps=3.0,
        entry_fee_bps=1.5,
        exit_fee_bps=1.5,
        round_trip_fee_bps=3.0,
        fee_breakeven_hours=0.05,
        long_order_type="post_only_limit",
        short_order_type="market",
        long_mark_price=65_000,
        short_mark_price=65_000,
        min_open_interest=1_000_000,
        observed_at=datetime.now(timezone.utc),
    )


def test_fee_schedule_uses_requested_venue_policies():
    schedule = FeeSchedule()
    assert schedule.rate_for("lighter", "market") == 0
    assert schedule.rate_for("lighter", "post_only_limit") == 0
    assert schedule.order_type_for("hyperliquid") == "post_only_limit"
    assert schedule.rate_for("hyperliquid", "post_only_limit") == 1.5
    assert schedule.order_type_for("aevo") == "market"
    assert schedule.rate_for("aevo", "market") == 8.0


def test_open_and_close_charge_each_leg_with_the_correct_fee():
    manager = ExecutionManager()
    item = opportunity("hyperliquid", "lighter")

    opened = manager.open_pair(item, 1_000)
    closed = manager.close_pair(item, 1_000)

    assert [leg.order_type for leg in opened.legs] == ["post_only_limit", "market"]
    assert [leg.fee_bps for leg in opened.legs] == [1.5, 0]
    assert [leg.fee_bps for leg in closed.legs] == [1.5, 0]
    assert all(leg.symbol == "BTC-PERP" for leg in opened.legs)
    assert all(leg.symbol == "BTC-PERP" for leg in closed.legs)
    assert sum(leg.fee_usd for leg in opened.legs) == pytest.approx(0.135)
    assert opened.matched_size_usd == pytest.approx(900)
    assert opened.temporary_exposure_usd == pytest.approx(900)
    assert closed.matched_size_usd == pytest.approx(900)
    assert sum(leg.fee_usd for leg in closed.legs) == pytest.approx(0.135)


def test_aevo_market_legs_use_standard_taker_fee():
    result = ExecutionManager().open_pair(opportunity("lighter", "aevo"), 1_000)
    assert [leg.order_type for leg in result.legs] == ["market", "market"]
    assert [leg.fee_bps for leg in result.legs] == [0, 8.0]
    assert sum(leg.fee_usd for leg in result.legs) == pytest.approx(0.8)


def test_paper_fill_uses_book_sides_slippage_and_partial_post_only_fill():
    manager = ExecutionManager()
    item = replace(
        opportunity("hyperliquid", "lighter"),
        long_bid_price=64_990,
        long_ask_price=65_010,
        short_bid_price=64_980,
        short_ask_price=65_020,
    )

    result = manager.open_pair(item, 1_000)

    assert result.legs[0].price == pytest.approx(64_990)
    assert result.legs[1].price == pytest.approx(64_980 * (1 - 0.0001))
    assert result.legs[0].size_usd == pytest.approx(900)
    assert result.legs[1].size_usd == pytest.approx(900)
    assert result.legs[0].status == "paper_partial"
    assert result.temporary_exposure_usd == pytest.approx(900)
