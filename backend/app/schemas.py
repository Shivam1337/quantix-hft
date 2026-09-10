from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


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


class FundingSettlementRead(ReadModel):
    id: int
    venue: str
    symbol: str
    funding_rate: float
    funding_rate_native: float | None
    funding_interval_hours: float
    funding_cycle_at: datetime
    settled_at: datetime
    source: str
    created_at: datetime


class TradeLogRead(ReadModel):
    id: int
    position_id: str | None
    symbol: str | None = None
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


class OpenPositionRequest(BaseModel):
    opportunity_id: str
    capital_usd: float = Field(gt=0)
    paper: bool = True


class ClosePositionRequest(BaseModel):
    reason: str = "manual close"


class SimulatorRequest(BaseModel):
    opportunity_id: str
    capital_usd: float = Field(gt=0, description="Total margin across both legs")
    holding_days: float = Field(default=30, gt=0, le=3650)
    leverage: float = Field(default=3.0, ge=1, le=100)


class SimulatorResponse(BaseModel):
    opportunity_id: str
    capital_usd: float
    holding_days: float
    leverage: float
    leg_margin_usd: float
    leg_notional_usd: float
    expected_filled_leg_notional_usd: float
    expected_fill_ratio: float
    position_notional_usd: float
    projected_hourly_cashflow_usd: float
    projected_period_funding_usd: float
    estimated_round_trip_fees_usd: float
    projected_net_profit_usd: float
    estimated_entry_fees_usd: float
    estimated_exit_fees_usd: float
    estimated_slippage_usd: float
    fee_breakeven_hours: float | None
    projected_return_pct: float


class SettingsRead(ReadModel):
    id: int
    min_apr: float
    min_open_interest: float
    basis_threshold_bps: float
    auto_unwind: bool
    negative_hours_to_unwind: int
    entry_min_history_snapshots: int
    entry_min_spread_stability_pct: float
    entry_expected_holding_hours: float
    simulation_min_capital_usd: float
    simulation_max_drawdown_pct: float
    alert_webhook_url: str | None


class SettingsUpdate(BaseModel):
    min_apr: float | None = Field(default=None, ge=-10_000, le=10_000)
    min_open_interest: float | None = Field(default=None, ge=0)
    basis_threshold_bps: float | None = Field(default=None, gt=0, le=10_000)
    auto_unwind: bool | None = None
    negative_hours_to_unwind: int | None = Field(default=None, ge=1, le=168)
    entry_min_history_snapshots: int | None = Field(default=None, ge=2, le=100)
    entry_min_spread_stability_pct: float | None = Field(default=None, ge=0, le=100)
    entry_expected_holding_hours: float | None = Field(default=None, gt=0, le=8_760)
    simulation_min_capital_usd: float | None = Field(default=None, gt=0)
    simulation_max_drawdown_pct: float | None = Field(default=None, ge=0, le=100)
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
    funding_rate: float | None = None
    funding_rate_native: float | None = None
    funding_interval_hours: float = 1.0
    funding_cycle_at: datetime | None = None
    funding_rate_source: str = "confirmed_history"
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
    leverage: float
    run_id: int | None = None
    updated_at: datetime


class SimulationResetResponse(BaseModel):
    status: str
    message: str
    account: SimulationAccountRead


class WalletImportRequest(BaseModel):
    private_key: SecretStr


class WalletAssetRead(BaseModel):
    symbol: str
    total: float
    available: float | None = None
    locked: float | None = None
class WalletBalanceRead(BaseModel):
    exchange_id: str
    exchange_name: str
    status: str
    total_usd: float | None = None
    available_usd: float | None = None
    assets: list[WalletAssetRead] = []
    message: str | None = None


class WalletRead(BaseModel):
    connected: bool
    address: str | None = None
    imported_at: datetime | None = None
    refreshed_at: datetime | None = None
    balances: list[WalletBalanceRead]
    live_trading_enabled: bool
    message: str | None = None
