export type Opportunity = {
  id: string;
  symbol: string;
  long_venue: string;
  short_venue: string;
  long_funding_rate: number;
  short_funding_rate: number;
  gross_hourly_rate: number;
  net_hourly_rate: number;
  net_apr_pct: number;
  basis_bps: number;
  capacity_usd: number;
  fee_bps: number;
  entry_fee_bps: number;
  exit_fee_bps: number;
  round_trip_fee_bps: number;
  fee_breakeven_hours: number | null;
  long_order_type: string;
  short_order_type: string;
  long_mark_price: number;
  short_mark_price: number;
  min_open_interest: number;
  observed_at: string;
  eligible: boolean;
  historical_3d_apr_pct?: number | null;
  historical_snapshots_count?: number;
  spread_stability_pct?: number | null;
  funding_rate_source?: string;
  funding_history_latest_cycle?: string | null;
};

export type Position = {
  id: string;
  opportunity_id: string;
  symbol: string;
  long_venue: string;
  short_venue: string;
  size_usd: number;
  leg_size_usd?: number | null;
  long_entry_price: number;
  short_entry_price: number;
  entry_basis_bps: number;
  current_long_price?: number | null;
  current_short_price?: number | null;
  current_basis_bps?: number | null;
  funding_pnl_usd: number;
  long_funding_pnl_usd?: number;
  short_funding_pnl_usd?: number;
  settled_funding_pnl_usd?: number;
  settled_long_funding_pnl_usd?: number;
  settled_short_funding_pnl_usd?: number;
  accrued_funding_pnl_usd?: number;
  accrued_long_funding_pnl_usd?: number;
  accrued_short_funding_pnl_usd?: number;
  basis_pnl_usd: number;
  entry_fee_usd: number;
  exit_fee_usd: number;
  fees_usd: number;
  status: string;
  negative_hours: number;
  opened_at: string;
  updated_at: string;
  open_reason?: string | null;
  closed_at: string | null;
  close_reason: string | null;
  entry_net_apr_pct?: number | null;
  entry_historical_apr_pct?: number | null;
  entry_long_funding_rate?: number | null;
  entry_short_funding_rate?: number | null;
  entry_rate_observed_at?: string | null;
  last_net_apr_pct?: number | null;
  last_long_funding_rate?: number | null;
  last_short_funding_rate?: number | null;
  last_rate_observed_at?: string | null;
};

export type SimulationAccount = {
  id: number;
  initial_balance: number;
  current_balance: number;
  allocated_balance: number;
  total_realized_pnl: number;
  updated_at: string;
};

export type Settings = {
  id: number;
  min_apr: number;
  min_open_interest: number;
  basis_threshold_bps: number;
  auto_unwind: boolean;
  entry_min_history_snapshots: number;
  entry_min_spread_stability_pct: number;
  alert_webhook_url: string | null;
};

export type Simulation = {
  projected_hourly_cashflow_usd: number;
  projected_period_funding_usd: number;
  estimated_round_trip_fees_usd: number;
  estimated_entry_fees_usd: number;
  estimated_exit_fees_usd: number;
  projected_net_profit_usd: number;
  fee_breakeven_hours: number | null;
  projected_return_pct: number;
};

export type SystemMetrics = {
  system_cpu_pct: number;
  system_ram_pct: number;
  system_ram_used_gb: number;
  system_ram_total_gb: number;
  system_disk_pct: number;
  system_disk_used_gb: number;
  system_disk_total_gb: number;
  process_cpu_pct: number;
  process_ram_mb: number;
  process_ram_pct: number;
  postgres_size_bytes: number | null;
  postgres_size_human: string;
  redis_size_bytes: number | null;
  redis_size_human: string;
  timestamp: string;
};

export type NavigationPage =
  | "dashboard"
  | "opportunities"
  | "positions"
  | "exchanges"
  | "throughput"
  | "simulator"
  | "settings";

export type ThemeMode = "dark" | "light" | "system";

export type ThroughputMinute = {
  timestamp: string;
  minute: number;
  counts: Record<string, number>;
  total: number;
};

export type RestEndpointStat = {
  method: string;
  endpoint: string;
  calls_total: number;
  last_called_at: string | null;
  last_status: number;
};

export type ThroughputSummary = {
  venues: string[];
  current_rates: Record<string, number>;
  total_processed: Record<string, number>;
  history: ThroughputMinute[];
  rest_current_rates?: Record<string, number>;
  rest_total_processed?: Record<string, number>;
  rest_history?: ThroughputMinute[];
  rest_endpoints?: Record<string, RestEndpointStat[]>;
  timestamp: string;
};

export type ExchangeMarket = {
  venue: string;
  symbol: string;
  funding_rate: number | null;
  funding_rate_native: number | null;
  funding_interval_hours: number;
  funding_cycle_at: string | null;
  funding_rate_source: string;
  mark_price: number;
  open_interest: number;
  bid: number;
  ask: number;
  observed_at: string;
};

export type ExchangeSummary = {
  id: string;
  name: string;
  status: string;
  markets_count: number;
  symbols: string[];
  markets: ExchangeMarket[];
};

export type WalletAsset = {
  symbol: string;
  total: number;
  available: number | null;
  locked: number | null;
};

export type WalletExchangeBalance = {
  exchange_id: string;
  exchange_name: string;
  status: string;
  total_usd: number | null;
  available_usd: number | null;
  assets: WalletAsset[];
  message: string | null;
};

export type WalletSnapshot = {
  connected: boolean;
  address: string | null;
  imported_at: string | null;
  refreshed_at: string | null;
  balances: WalletExchangeBalance[];
  live_trading_enabled: boolean;
  message: string | null;
};

export type FundingSettlement = {
  id: number;
  venue: string;
  symbol: string;
  funding_rate: number;
  funding_rate_native: number | null;
  funding_interval_hours: number;
  funding_cycle_at: string;
  settled_at: string;
  source: string;
  created_at: string;
};

export type TradeLog = {
  id: number;
  position_id: string | null;
  venue: string;
  side: string;
  order_type: string;
  size_usd: number;
  price: number;
  status: string;
  client_order_id: string;
  phase: string;
  fee_bps: number;
  fee_usd: number;
  error: string | null;
  created_at: string;
};

export type FundingPayment = {
  id: number;
  position_id: string;
  symbol: string;
  cycle_at: string;
  long_venue: string;
  long_rate: number;
  long_payment_usd: number;
  short_venue: string;
  short_rate: number;
  short_payment_usd: number;
  net_payment_usd: number;
  settlement_type: string;
  rate_source: string;
  created_at: string;
};
