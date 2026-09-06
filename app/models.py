"""
Pydantic data schemas for API requests, responses, and internal state.
"""
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class OrderBookLevel(BaseModel):
    price: float
    size: float


class ExchangeQuote(BaseModel):
    symbol: str
    mid_price: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0
    fees: str = ""
    status: str = "INITIALIZING"
    lag_vs_leader: float = 0.0
    lag_bps: float = 0.0
    bids: List[List[Any]] = Field(default_factory=list)
    asks: List[List[Any]] = Field(default_factory=list)


class TradeDecision(BaseModel):
    stance: str = "MONITORING"  # IDLE, MONITORING, SIGNAL_DETECTED, IN_POSITION, COOLDOWN
    action: str = "NONE"        # NONE, SNIPE_LONG, SNIPE_SHORT, HOLD, CLOSE
    target_exchange: str = "Lighter.xyz (0% Fee DEX)"
    elected_leader: str = "Binance"
    signal_strength_usd: float = 0.0
    rationale: str = "Awaiting dynamic discovery breakout..."
    rejection_reason: Optional[str] = None
    target_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    timestamp: str = ""


class ActivePosition(BaseModel):
    side: str                   # LONG or SHORT
    exchange: str = "Lighter.xyz"
    leader_name: Optional[str] = "Binance"
    size_btc: float = 0.05
    entry_price: float
    current_price: float
    target_price: float
    stop_loss_price: float
    expected_lag: float
    floating_pnl_usd: float
    entry_time: str
    hold_seconds: float
    exit_conditions: Dict[str, str] = Field(default_factory=dict)
    # Capital & Leverage Attributes
    margin_allocated_usd: float = 50.0
    leverage: float = 50.0
    notional_usd: float = 2500.0


class ClosedTrade(BaseModel):
    id: int
    time: str
    side: str
    leader: Optional[str] = "Binance"
    size_btc: float
    entry_px: float
    exit_px: float
    gross_pnl: float
    fees_paid: float = 0.0
    net_pnl: float
    hold_sec: float
    reason: str
    is_win: bool
    # Capital & Leverage Attributes
    margin_allocated_usd: float = 50.0
    leverage: float = 50.0
    notional_usd: float = 2500.0


class TradingPerformance(BaseModel):
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_pnl: float = 0.0
    fees_saved_vs_poly: float = 0.0
    net_pnl: float = 0.0
    avg_hold_sec: float = 0.0
    profit_factor: float = 0.0
    # Dynamic Account & Capital Management
    account_base_balance_usd: float = 100.0
    account_balance_usd: float = 100.0
    account_equity_usd: float = 100.0
    margin_used_usd: float = 0.0
    free_margin_usd: float = 100.0
    leverage: float = 50.0
    margin_utilization_pct: float = 50.0
    target_notional_usd: float = 2500.0
    return_on_margin_pct: float = 0.0


class LeadLagMetrics(BaseModel):
    dynamic_leader: str = "Binance"
    leader_price: float = 0.0
    adj_leader_price: float = 0.0
    leader_velocity: float = 0.0
    baseline_basis_usd: float = 0.0
    consensus_status: str = "MODERATE"
    consensus_agreement: str = ""
    leader_selection_reason: str = ""
    venues_velocities: Dict[str, float] = Field(default_factory=dict)
    binance_hl_spread_usd: float = 0.0
    lighter_lag_vs_leader_usd: float = 0.0
    lighter_lag_vs_leader_bps: float = 0.0
    lighter_state: str = "ALIGNED"
    poly_lag_vs_leader_usd: float = 0.0
    poly_lag_vs_leader_bps: float = 0.0
    poly_state: str = "ALIGNED"
    avg_catchup_latency_sec: float = 0.0
    total_repricing_events: int = 0



class RepricingEvent(BaseModel):
    id: int
    timestamp: str
    leading_exchange: str
    lagging_exchange: str
    direction: str
    initial_lag_usd: float
    catchup_seconds: float
    resolved: bool


class SystemHealth(BaseModel):
    status: str = "HEALTHY"
    uptime_seconds: float = 0.0
    uptime_formatted: str = "00:00:00"
    start_time: str = ""
    feeds: Dict[str, str] = Field(default_factory=dict)
    messages_processed: int = 0
    tick_rate_hz: float = 0.0
    active_sse_clients: int = 0
    streaming_feeds: int = 0
    total_feeds: int = 0
    resources: Dict[str, Any] = Field(default_factory=dict)
    persistence: Dict[str, Any] = Field(default_factory=dict)
