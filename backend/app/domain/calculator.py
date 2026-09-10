from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from app.domain.fees import FeeSchedule
from app.domain.types import MarketSnapshotData, OpportunityData


@dataclass(frozen=True)
class CalculatorConfig:
    fee_schedule: FeeSchedule = field(default_factory=FeeSchedule)
    capacity_fraction: float = 0.05
    fee_amortization_hours: float = 24
    min_history_cycles: int = 6
    expected_holding_hours: float = 24


def _opportunity_id(symbol: str, long_venue: str, short_venue: str) -> str:
    return f"{symbol}:{long_venue}:{short_venue}"


def _historical_stat(historical_stats: dict, symbol: str, first: str, second: str):
    direct = historical_stats.get((symbol.upper(), first.lower(), second.lower()))
    return direct or historical_stats.get((symbol.upper(), second.lower(), first.lower()))


def _make_opportunity(
    first_leg: MarketSnapshotData,
    second_leg: MarketSnapshotData,
    config: CalculatorConfig,
    historical_stats: dict,
) -> OpportunityData | None:
    stat = _historical_stat(
        historical_stats, first_leg.symbol, first_leg.venue, second_leg.venue
    )
    if stat is None:
        return None

    live_by_venue = {first_leg.venue.lower(): first_leg, second_leg.venue.lower(): second_leg}
    long_leg = live_by_venue.get(stat.long_venue.lower())
    short_leg = live_by_venue.get(stat.short_venue.lower())
    if long_leg is None or short_leg is None:
        return None

    # These rates are rolling medians of aligned confirmed cycles. No live
    # funding value is read from MarketSnapshotData.
    long_rate = stat.long_avg_rate
    short_rate = stat.short_avg_rate
    gross = stat.historical_gross_hourly
    entry_fee_bps = config.fee_schedule.pair_fee_bps(long_leg.venue, short_leg.venue)
    exit_fee_bps = entry_fee_bps
    round_trip_fee_bps = entry_fee_bps + exit_fee_bps
    round_trip_fee = round_trip_fee_bps / 10_000
    holding_hours = config.expected_holding_hours or config.fee_amortization_hours
    fee_drag = round_trip_fee / holding_hours
    net_hourly = gross - fee_drag
    net_apr = net_hourly * 24 * 365 * 100
    fee_breakeven = round_trip_fee / gross if gross > 0 else None
    average_price = (long_leg.mark_price + short_leg.mark_price) / 2
    basis_bps = ((short_leg.mark_price - long_leg.mark_price) / average_price) * 10_000
    min_oi = min(long_leg.open_interest, short_leg.open_interest)

    return OpportunityData(
        id=_opportunity_id(long_leg.symbol, long_leg.venue, short_leg.venue),
        symbol=long_leg.symbol,
        long_venue=long_leg.venue,
        short_venue=short_leg.venue,
        long_funding_rate=long_rate,
        short_funding_rate=short_rate,
        gross_hourly_rate=gross,
        net_hourly_rate=net_hourly,
        net_apr_pct=net_apr,
        basis_bps=basis_bps,
        capacity_usd=min_oi * config.capacity_fraction,
        fee_bps=round_trip_fee_bps,
        entry_fee_bps=entry_fee_bps,
        exit_fee_bps=exit_fee_bps,
        round_trip_fee_bps=round_trip_fee_bps,
        fee_breakeven_hours=fee_breakeven,
        long_order_type=config.fee_schedule.order_type_for(long_leg.venue),
        short_order_type=config.fee_schedule.order_type_for(short_leg.venue),
        long_mark_price=long_leg.mark_price,
        short_mark_price=short_leg.mark_price,
        min_open_interest=min_oi,
        observed_at=max(long_leg.observed_at, short_leg.observed_at),
        historical_3d_apr_pct=net_apr,
        historical_snapshots_count=stat.snapshot_count,
        spread_stability_pct=stat.spread_stability_pct,
        funding_rate_source="confirmed_history",
        funding_history_latest_cycle=stat.latest_cycle,
        expected_holding_hours=holding_hours,
        recent_gross_hourly_rate=getattr(stat, "recent_gross_hourly", None),
        recent_net_hourly_rate=(
            getattr(stat, "recent_gross_hourly", None) - fee_drag
            if getattr(stat, "recent_gross_hourly", None) is not None
            else None
        ),
        recent_spread_stability_pct=getattr(stat, "recent_spread_stability_pct", None),
        long_bid_price=long_leg.bid,
        long_ask_price=long_leg.ask,
        short_bid_price=short_leg.bid,
        short_ask_price=short_leg.ask,
    )


def calculate_opportunities(
    snapshots: list[MarketSnapshotData],
    config: CalculatorConfig,
    historical_stats: dict | None = None,
) -> list[OpportunityData]:
    """Rank opportunities using confirmed history; missing history means no pair."""
    if not historical_stats:
        return []
    grouped: dict[str, list[MarketSnapshotData]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[snapshot.symbol.upper()].append(snapshot)

    opportunities: list[OpportunityData] = []
    for symbol_snapshots in grouped.values():
        for first, second in combinations(symbol_snapshots, 2):
            value = _make_opportunity(first, second, config, historical_stats)
            if value is not None:
                opportunities.append(value)
    return sorted(opportunities, key=lambda item: (item.net_apr_pct, item.id), reverse=True)
