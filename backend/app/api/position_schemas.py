from datetime import datetime

from app.schemas import ReadModel


class PositionRead(ReadModel):
    id: str
    opportunity_id: str
    symbol: str
    long_venue: str
    short_venue: str
    size_usd: float
    leg_size_usd: float | None = None
    long_entry_price: float
    short_entry_price: float
    entry_basis_bps: float
    current_long_price: float | None = None
    current_short_price: float | None = None
    current_basis_bps: float | None = None
    funding_pnl_usd: float
    long_funding_pnl_usd: float = 0.0
    short_funding_pnl_usd: float = 0.0
    settled_funding_pnl_usd: float = 0.0
    settled_long_funding_pnl_usd: float = 0.0
    settled_short_funding_pnl_usd: float = 0.0
    accrued_funding_pnl_usd: float = 0.0
    accrued_long_funding_pnl_usd: float = 0.0
    accrued_short_funding_pnl_usd: float = 0.0
    basis_pnl_usd: float
    entry_fee_usd: float
    exit_fee_usd: float
    fees_usd: float
    status: str
    negative_hours: int
    opened_at: datetime
    updated_at: datetime
    open_reason: str | None = None
    closed_at: datetime | None
    close_reason: str | None
    entry_net_apr_pct: float | None = None
    entry_historical_apr_pct: float | None = None
    entry_long_funding_rate: float | None = None
    entry_short_funding_rate: float | None = None
    entry_rate_observed_at: datetime | None = None
    last_net_apr_pct: float | None = None
    last_long_funding_rate: float | None = None
    last_short_funding_rate: float | None = None
    last_rate_observed_at: datetime | None = None


class FundingPaymentRead(ReadModel):
    id: int
    position_id: str
    symbol: str
    cycle_at: datetime
    long_venue: str
    long_rate: float
    long_payment_usd: float
    short_venue: str
    short_rate: float
    short_payment_usd: float
    net_payment_usd: float
    settlement_type: str = "simulated"
    rate_source: str = "market_snapshot"
    created_at: datetime
