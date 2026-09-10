from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.entry import entry_rejection_reason
from app.domain.positioning import opportunity_for_position
from app.domain.types import MarketSnapshotData
from app.services.historical_funding import HistoricalSpreadStats


def snapshot(venue: str) -> MarketSnapshotData:
    observed_at = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    return MarketSnapshotData(
        venue=venue,
        symbol="BTC-PERP",
        mark_price=65_000,
        open_interest=1_000_000,
        bid=64_999,
        ask=65_001,
        observed_at=observed_at,
    )


def stats(
    long_rate: float,
    short_rate: float,
    long_venue: str = "hyperliquid",
    short_venue: str = "lighter",
    count: int = 6,
):
    latest = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    gross = short_rate - long_rate
    return HistoricalSpreadStats(
        long_venue=long_venue,
        short_venue=short_venue,
        long_avg_rate=long_rate,
        short_avg_rate=short_rate,
        historical_gross_hourly=gross,
        historical_net_apr_pct=gross * 24 * 365 * 100,
        snapshot_count=count,
        spread_stability_pct=100,
        oldest_cycle=latest - timedelta(hours=count - 1),
        latest_cycle=latest,
    )


def ranked(long_rate=0.0001, short_rate=0.0002):
    return calculate_opportunities(
        [snapshot("hyperliquid"), snapshot("lighter")],
        CalculatorConfig(),
        {
            ("BTC-PERP", "hyperliquid", "lighter"): stats(
                long_rate, short_rate, "hyperliquid", "lighter"
            )
        },
    )


def settings():
    return SimpleNamespace(
        min_apr=1,
        min_open_interest=100_000,
        basis_threshold_bps=75,
        entry_min_history_snapshots=6,
        entry_min_spread_stability_pct=60,
    )


def test_position_is_reoriented_when_ranked_direction_flips():
    ranked_values = ranked()
    position = SimpleNamespace(
        opportunity_id="BTC-PERP:lighter:hyperliquid",
        symbol="BTC-PERP",
        long_venue="lighter",
        short_venue="hyperliquid",
    )

    current = opportunity_for_position(position, ranked_values)

    assert current is not None
    assert current.id == position.opportunity_id
    assert current.long_venue == "lighter"
    assert current.short_venue == "hyperliquid"
    assert current.net_apr_pct < 0


def test_entry_policy_rejects_missing_confirmed_history():
    value = ranked()[0]
    value = replace(value, historical_3d_apr_pct=None)
    reason = entry_rejection_reason(value, settings())
    assert reason == "waiting for confirmed funding history"


@pytest.mark.parametrize("history_count", [2, 4])
def test_entry_policy_rejects_insufficient_confirmed_history(history_count):
    value = replace(
        ranked()[0],
        historical_3d_apr_pct=10,
        historical_snapshots_count=history_count,
        spread_stability_pct=100,
    )
    reason = entry_rejection_reason(value, settings())
    assert reason is not None
    assert "historical snapshots" in reason


def test_entry_policy_does_not_compare_against_a_live_rate():
    value = replace(
        ranked(long_rate=0.0, short_rate=0.0002)[0],
        historical_3d_apr_pct=10,
        historical_snapshots_count=6,
        spread_stability_pct=100,
        net_apr_pct=10,
    )
    assert entry_rejection_reason(value, settings()) is None


def test_entry_policy_rejects_missing_spread_stability():
    value = replace(
        ranked()[0],
        historical_3d_apr_pct=10,
        historical_snapshots_count=6,
        spread_stability_pct=None,
    )
    reason = entry_rejection_reason(value, settings())
    assert reason == "waiting for funding spread stability history"


def test_entry_policy_rejects_recent_funding_deterioration():
    value = replace(ranked()[0], recent_net_hourly_rate=-0.000001)
    reason = entry_rejection_reason(value, settings())
    assert reason == "recent funding spread does not recover fees"


def test_entry_policy_rejects_fee_recovery_longer_than_expected_hold():
    value = ranked()[0]
    policy = SimpleNamespace(**settings().__dict__, entry_expected_holding_hours=2)
    reason = entry_rejection_reason(value, policy)
    assert reason is not None
    assert "expected holding horizon" in reason
