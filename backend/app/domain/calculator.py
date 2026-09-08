from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from app.domain.fees import FeeSchedule
from app.domain.types import MarketSnapshotData, OpportunityData


@dataclass(frozen=True)
class CalculatorConfig:
    fee_schedule: FeeSchedule = field(default_factory=FeeSchedule)
    capacity_fraction: float = 0.05
    fee_amortization_hours: float = 24 * 30


def _opportunity_id(symbol: str, long_venue: str, short_venue: str) -> str:
    return f"{symbol}:{long_venue}:{short_venue}"


def _make_opportunity(
    long_leg: MarketSnapshotData,
    short_leg: MarketSnapshotData,
    config: CalculatorConfig,
    historical_stats: dict | None = None,
) -> OpportunityData:
    gross = short_leg.funding_rate - long_leg.funding_rate
    long_order_type = config.fee_schedule.order_type_for(long_leg.venue)
    short_order_type = config.fee_schedule.order_type_for(short_leg.venue)
    entry_fee_bps = config.fee_schedule.pair_fee_bps(long_leg.venue, short_leg.venue)
    exit_fee_bps = entry_fee_bps
    round_trip_fee_bps = entry_fee_bps + exit_fee_bps
    round_trip_fee = round_trip_fee_bps / 10_000
    fee_drag = round_trip_fee / config.fee_amortization_hours
    net_hourly = gross - fee_drag
    fee_breakeven = round_trip_fee / gross if gross > 0 else None
    average_price = (long_leg.mark_price + short_leg.mark_price) / 2
    basis_bps = ((short_leg.mark_price - long_leg.mark_price) / average_price) * 10_000
    min_oi = min(long_leg.open_interest, short_leg.open_interest)

    hist_apr = None
    hist_count = 0
    hist_stab = None
    if historical_stats:
        sym = long_leg.symbol.upper()
        lv = long_leg.venue.lower()
        sv = short_leg.venue.lower()
        stat = historical_stats.get((sym, lv, sv)) or historical_stats.get((sym, sv, lv))
        if stat is not None:
            hist_apr = stat.historical_net_apr_pct
            hist_count = stat.snapshot_count
            hist_stab = stat.spread_stability_pct

    return OpportunityData(
        id=_opportunity_id(long_leg.symbol, long_leg.venue, short_leg.venue),
        symbol=long_leg.symbol,
        long_venue=long_leg.venue,
        short_venue=short_leg.venue,
        long_funding_rate=long_leg.funding_rate,
        short_funding_rate=short_leg.funding_rate,
        gross_hourly_rate=gross,
        net_hourly_rate=net_hourly,
        net_apr_pct=net_hourly * 24 * 365 * 100,
        basis_bps=basis_bps,
        capacity_usd=min_oi * config.capacity_fraction,
        fee_bps=round_trip_fee_bps,
        entry_fee_bps=entry_fee_bps,
        exit_fee_bps=exit_fee_bps,
        round_trip_fee_bps=round_trip_fee_bps,
        fee_breakeven_hours=fee_breakeven,
        long_order_type=long_order_type,
        short_order_type=short_order_type,
        long_mark_price=long_leg.mark_price,
        short_mark_price=short_leg.mark_price,
        min_open_interest=min_oi,
        observed_at=max(long_leg.observed_at, short_leg.observed_at),
        historical_3d_apr_pct=hist_apr,
        historical_snapshots_count=hist_count,
        spread_stability_pct=hist_stab,
    )


def calculate_opportunities(
    snapshots: list[MarketSnapshotData],
    config: CalculatorConfig,
    historical_stats: dict | None = None,
) -> list[OpportunityData]:
    grouped: dict[str, list[MarketSnapshotData]] = defaultdict(list)
    for snapshot in snapshots:
        grouped[snapshot.symbol].append(snapshot)

    opportunities: list[OpportunityData] = []
    for symbol_snapshots in grouped.values():
        for first, second in combinations(symbol_snapshots, 2):
            if first.funding_rate <= second.funding_rate:
                long_leg, short_leg = first, second
            else:
                long_leg, short_leg = second, first
            opportunities.append(_make_opportunity(long_leg, short_leg, config, historical_stats))
    return sorted(
        opportunities,
        key=lambda item: (
            item.historical_3d_apr_pct if item.historical_3d_apr_pct is not None else item.net_apr_pct,
            item.net_apr_pct,
        ),
        reverse=True,
    )

