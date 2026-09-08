from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.entry import entry_rejection_reason
from app.domain.positioning import opportunity_for_position
from app.domain.types import MarketSnapshotData


def snapshot(venue: str, rate: float) -> MarketSnapshotData:
    observed_at = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    return MarketSnapshotData(
        venue=venue,
        symbol="BTC-PERP",
        funding_rate=rate,
        mark_price=65_000,
        open_interest=1_000_000,
        bid=64_999,
        ask=65_001,
        observed_at=observed_at,
        funding_rate_native=rate,
        funding_interval_hours=1,
        funding_cycle_at=observed_at,
    )


def test_position_is_reoriented_when_ranked_direction_flips():
    ranked = calculate_opportunities(
        [snapshot("hyperliquid", 0.0002), snapshot("lighter", 0.0001)],
        CalculatorConfig(),
    )
    position = SimpleNamespace(
        opportunity_id="BTC-PERP:hyperliquid:lighter",
        symbol="BTC-PERP",
        long_venue="hyperliquid",
        short_venue="lighter",
    )

    current = opportunity_for_position(position, ranked)

    assert current is not None
    assert current.id == position.opportunity_id
    assert current.long_venue == "hyperliquid"
    assert current.short_venue == "lighter"
    assert current.net_apr_pct < 0


def test_entry_policy_rejects_a_spot_apr_spike():
    ranked = calculate_opportunities(
        [snapshot("hyperliquid", 0.00001), snapshot("lighter", 0.0002)],
        CalculatorConfig(),
    )[0]
    ranked = replace(ranked, historical_3d_apr_pct=None)

    settings = SimpleNamespace(
        min_apr=10,
        min_open_interest=100_000,
        basis_threshold_bps=75,
        entry_min_history_snapshots=6,
        entry_min_spread_stability_pct=60,
        entry_max_apr_ratio=2,
    )

    reason = entry_rejection_reason(ranked, settings)

    assert reason == "waiting for 3-day funding history"


@pytest.mark.parametrize("history_count", [2, 4])
def test_entry_policy_rejects_insufficient_history(history_count):
    ranked = calculate_opportunities(
        [snapshot("hyperliquid", 0.00001), snapshot("lighter", 0.00002)],
        CalculatorConfig(),
    )[0]
    ranked = replace(
        ranked,
        historical_3d_apr_pct=10,
        historical_snapshots_count=history_count,
        spread_stability_pct=100,
    )
    settings = SimpleNamespace(
        min_apr=1,
        min_open_interest=100_000,
        basis_threshold_bps=75,
        entry_min_history_snapshots=6,
        entry_min_spread_stability_pct=60,
        entry_max_apr_ratio=2,
    )

    reason = entry_rejection_reason(ranked, settings)

    assert reason is not None
    assert "historical snapshots" in reason


def test_entry_policy_rejects_current_apr_that_is_far_above_history():
    ranked = calculate_opportunities(
        [snapshot("hyperliquid", 0.00001), snapshot("lighter", 0.00004)],
        CalculatorConfig(),
    )[0]
    ranked = replace(
        ranked,
        historical_3d_apr_pct=10,
        historical_snapshots_count=6,
        spread_stability_pct=100,
    )
    settings = SimpleNamespace(
        min_apr=1,
        min_open_interest=100_000,
        basis_threshold_bps=75,
        entry_min_history_snapshots=6,
        entry_min_spread_stability_pct=60,
        entry_max_apr_ratio=2,
    )

    reason = entry_rejection_reason(ranked, settings)

    assert reason is not None
    assert "more than 2.0x" in reason


def test_entry_policy_rejects_missing_spread_stability():
    ranked = calculate_opportunities(
        [snapshot("hyperliquid", 0.00001), snapshot("lighter", 0.00002)],
        CalculatorConfig(),
    )[0]
    ranked = replace(ranked, historical_3d_apr_pct=10, historical_snapshots_count=6)
    settings = SimpleNamespace(
        min_apr=1,
        min_open_interest=100_000,
        basis_threshold_bps=75,
        entry_min_history_snapshots=6,
        entry_min_spread_stability_pct=60,
        entry_max_apr_ratio=2,
    )

    reason = entry_rejection_reason(ranked, settings)

    assert reason == "waiting for funding spread stability history"
