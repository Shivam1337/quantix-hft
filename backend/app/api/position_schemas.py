from datetime import datetime

from app.domain.pnl import position_pnl
from app.schemas import ReadModel


class PositionRead(ReadModel):
    id: str
    opportunity_id: str
    symbol: str
    long_venue: str
    short_venue: str
    size_usd: float
    leg_size_usd: float | None = None
    requested_size_usd: float | None = None
    execution_fill_ratio: float = 1.0
    temporary_exposure_usd: float = 0.0
    simulation_run_id: int | None = None
    long_entry_price: float
    short_entry_price: float
    entry_basis_bps: float
    current_long_price: float | None = None
    current_short_price: float | None = None
    current_basis_bps: float | None = None
    margin_per_leg_usd: float | None = None
    leverage: float = 1.0
    funding_pnl_usd: float
    long_funding_pnl_usd: float = 0.0
    short_funding_pnl_usd: float = 0.0
    settled_funding_pnl_usd: float = 0.0
    settled_long_funding_pnl_usd: float = 0.0
    settled_short_funding_pnl_usd: float = 0.0
    realized_basis_pnl_usd: float = 0.0
    accrued_funding_pnl_usd: float = 0.0
    accrued_long_funding_pnl_usd: float = 0.0
    accrued_short_funding_pnl_usd: float = 0.0
    basis_pnl_usd: float
    entry_fee_usd: float
    exit_fee_usd: float
    fees_usd: float
    gross_pnl_usd: float = 0.0
    paid_fees_usd: float = 0.0
    net_pnl_usd: float = 0.0
    estimated_close_fee_usd: float = 0.0
    estimated_net_pnl_if_closed_usd: float = 0.0
    estimated_net_proceeds_usd: float = 0.0
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


def to_position_read(position, opportunity=None) -> PositionRead:
    value = PositionRead.model_validate(position)
    pnl = position_pnl(position, opportunity)
    return value.model_copy(update=pnl.__dict__)


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
    settlement_type: str = "confirmed"
    rate_source: str = "exchange_history"
    created_at: datetime


class FundingPendingCycleRead(ReadModel):
    id: int
    position_id: str
    symbol: str
    cycle_at: datetime
    long_venue: str
    short_venue: str
    reason: str
    first_seen_at: datetime
    updated_at: datetime
