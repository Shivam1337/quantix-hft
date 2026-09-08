from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OpportunityRead(ReadModel):
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
    eligible: bool = True
    historical_3d_apr_pct: float | None = None
    historical_snapshots_count: int = 0
    spread_stability_pct: float | None = None


class FundingSnapshotRead(ReadModel):
    id: int
    venue: str
    symbol: str
    funding_rate: float
    funding_rate_native: float | None
    funding_interval_hours: float
    funding_cycle_at: datetime | None
    mark_price: float
    open_interest: float
    bid: float
    ask: float
    observed_at: datetime
    created_at: datetime


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
    funding_pnl_usd: float
    long_funding_pnl_usd: float = 0.0
    short_funding_pnl_usd: float = 0.0
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


class TradeLogRead(ReadModel):
    id: int
    position_id: str | None
    venue: str
    side: str
    order_type: str
    size_usd: float
    price: float
    status: str
    client_order_id: str
    phase: str
    fee_bps: float
    fee_usd: float
    error: str | None
    created_at: datetime


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
    created_at: datetime


class OpenPositionRequest(BaseModel):
    opportunity_id: str
    capital_usd: float = Field(gt=0)
    paper: bool = True


class ClosePositionRequest(BaseModel):
    reason: str = "manual close"


class SimulatorRequest(BaseModel):
    opportunity_id: str
    capital_usd: float = Field(gt=0)
    holding_days: float = Field(default=30, gt=0, le=3650)


class SimulatorResponse(BaseModel):
    opportunity_id: str
    capital_usd: float
    holding_days: float
    projected_hourly_cashflow_usd: float
    projected_period_funding_usd: float
    estimated_round_trip_fees_usd: float
    projected_net_profit_usd: float
    estimated_entry_fees_usd: float
    estimated_exit_fees_usd: float
    fee_breakeven_hours: float | None
    projected_return_pct: float


class SettingsRead(ReadModel):
    id: int
    min_apr: float
    min_open_interest: float
    basis_threshold_bps: float
    auto_unwind: bool
    alert_webhook_url: str | None


class SettingsUpdate(BaseModel):
    min_apr: float | None = Field(default=None, ge=-10_000, le=10_000)
    min_open_interest: float | None = Field(default=None, ge=0)
    basis_threshold_bps: float | None = Field(default=None, gt=0, le=10_000)
    auto_unwind: bool | None = None
    alert_webhook_url: str | None = None


class HealthRead(BaseModel):
    status: str
    environment: str
    data_mode: str
    scheduler_enabled: bool
    last_refresh: datetime | None = None


class RefreshRead(BaseModel):
    count: int
    refreshed_at: datetime


class WebSocketEnvelope(BaseModel):
    type: str
    data: dict


class SystemMetricsRead(BaseModel):
    system_cpu_pct: float
    system_ram_pct: float
    system_ram_used_gb: float
    system_ram_total_gb: float
    system_disk_pct: float
    system_disk_used_gb: float
    system_disk_total_gb: float
    process_cpu_pct: float
    process_ram_mb: float
    process_ram_pct: float
    postgres_size_bytes: int | None = None
    postgres_size_human: str
    redis_size_bytes: int | None = None
    redis_size_human: str
    timestamp: datetime


class ExchangeMarketRead(ReadModel):
    venue: str
    symbol: str
    funding_rate: float
    funding_rate_native: float | None = None
    funding_interval_hours: float
    mark_price: float
    open_interest: float
    bid: float
    ask: float
    observed_at: datetime


class ExchangeSummaryRead(BaseModel):
    id: str
    name: str
    status: str
    markets_count: int
    symbols: list[str]
    markets: list[ExchangeMarketRead]


class SimulationAccountRead(ReadModel):
    id: int
    initial_balance: float
    current_balance: float
    allocated_balance: float
    total_realized_pnl: float
    updated_at: datetime


class SimulationResetResponse(BaseModel):
    status: str
    message: str
    account: SimulationAccountRead



