from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketSnapshotData:
    venue: str
    symbol: str
    mark_price: float
    open_interest: float
    bid: float
    ask: float
    observed_at: datetime


@dataclass(frozen=True)
class FundingSettlementData:
    """A funding rate returned by an exchange's confirmed history endpoint."""

    venue: str
    symbol: str
    funding_rate: float
    funding_rate_native: float | None
    funding_interval_hours: float
    funding_cycle_at: datetime
    settled_at: datetime
    source: str = "exchange_history"


@dataclass(frozen=True)
class OpportunityData:
    id: str
    symbol: str
    long_venue: str
    short_venue: str
    long_funding_rate: float
    short_funding_rate: float
    gross_hourly_rate: float
    net_hourly_rate: float
    net_apr_pct: float
    basis_bps: float
    capacity_usd: float
    fee_bps: float
    entry_fee_bps: float
    exit_fee_bps: float
    round_trip_fee_bps: float
    fee_breakeven_hours: float | None
    long_order_type: str
    short_order_type: str
    long_mark_price: float
    short_mark_price: float
    min_open_interest: float
    observed_at: datetime
    historical_3d_apr_pct: float | None = None
    historical_snapshots_count: int = 0
    spread_stability_pct: float | None = None
    funding_rate_source: str = "confirmed_history"
    funding_history_latest_cycle: datetime | None = None
    expected_holding_hours: float = 24.0
    recent_gross_hourly_rate: float | None = None
    recent_net_hourly_rate: float | None = None
    recent_spread_stability_pct: float | None = None
    long_bid_price: float | None = None
    long_ask_price: float | None = None
    short_bid_price: float | None = None
    short_ask_price: float | None = None


@dataclass(frozen=True)
class RiskDecision:
    should_unwind: bool
    basis_breach: bool
    funding_flip: bool
    reason: str | None
