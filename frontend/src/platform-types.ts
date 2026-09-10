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
  symbol?: string | null;
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
