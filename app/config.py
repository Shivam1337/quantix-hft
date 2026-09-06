"""
Configuration settings for the HFT Lead-Lag & Snipe Engine.
"""
import os

# Server Settings
# Bind locally by default. Set SERVER_HOST explicitly only when remote access is intended.
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8765"))
# Keep HTTP/SSE connection shutdown bounded separately from the persistence
# drain. Docker grants 35s total, so a stuck client must not consume the time
# reserved for flushing derived PostgreSQL records.
UVICORN_GRACEFUL_SHUTDOWN_SECONDS = int(os.getenv("UVICORN_GRACEFUL_SHUTDOWN_SECONDS", "5"))
GRACEFUL_SHUTDOWN_SECONDS = int(os.getenv("GRACEFUL_SHUTDOWN_SECONDS", "25"))

# WebSocket Endpoints
BINANCE_WS_URL = "wss://fstream.binance.com/ws/btcusdt@bookTicker"
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
LIGHTER_WS_URL = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"
POLY_WS_URL = "wss://ws.perpetuals.polymarket.com/v1/ws"

# WebSocket Subscriptions
BYBIT_SUB_PAYLOAD = {"op": "subscribe", "args": ["tickers.BTCUSDT"]}
OKX_SUB_PAYLOAD = {"op": "subscribe", "args": [{"channel": "tickers", "instId": "BTC-USDT-SWAP"}]}
HL_SUB_PAYLOAD = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": "BTC"}}
LIGHTER_SUB_PAYLOAD = {"type": "subscribe", "channel": "order_book/1"}
POLY_SUB_PAYLOAD = {"req": "sub", "id": 1, "chs": ["book::6"]}

# HTTP Headers
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Strategy & Snipe Parameters
MIN_LAG_TRIGGER = 6.0         # Minimum $ lag between consensus leader and Lighter quote to trigger snipe
MIN_ENTRY_VELOCITY_USD = 2.0  # Minimum leader velocity ($ move in 2s window) to authorize entry
MAX_HOLD_SECONDS = 12.0       # Max duration to hold a snipe position if target not hit
COOLDOWN_SECONDS = 2.0        # Seconds to wait after trade exit before entering next trade
STOP_LOSS_DRAWDOWN = 20.0     # Hard stop loss if position moves $20 against us
REVERSAL_INVALIDATION = 4.0   # If leader moves $4 back past entry, signal was a false breakout
# A ladder may consume a few immediate L2 levels, but must retain a positive
# expected edge beyond the conservative target-exit threshold.
TARGET_EXIT_BUFFER_USD = max(0.0, float(os.getenv("TARGET_EXIT_BUFFER_USD", "1.0")))
LADDER_MIN_EXPECTED_PROFIT_USD = max(0.1, float(os.getenv("LADDER_MIN_EXPECTED_PROFIT_USD", "1.0")))
MAX_EXECUTION_BOOK_LEVELS = max(1, min(3, int(os.getenv("MAX_EXECUTION_BOOK_LEVELS", "3"))))
# Consume only this fraction of each profitable L2 level and of the configured
# notional cap. This leaves visible depth for normal book churn while retaining
# the same IOC price/profit boundary.
EXECUTION_LIQUIDITY_PARTICIPATION = min(
    1.0,
    max(0.0, float(os.getenv("EXECUTION_LIQUIDITY_PARTICIPATION", "0.50"))),
)

# Capital Management & Leverage Parameters
ACCOUNT_BASE_BALANCE_USD = float(os.getenv("ACCOUNT_BASE_BALANCE_USD", "100.0"))  # Base account equity ($100)
TRADE_MARGIN_FRACTION = float(os.getenv("TRADE_MARGIN_FRACTION", "0.50"))        # 50% margin allocation per trade ($50)
LEVERAGE = float(os.getenv("LEVERAGE", "50.0"))                                  # Up to 50x leverage on Lighter ($2,500 notional)
TRADE_SIZE_BTC = float(os.getenv("TRADE_SIZE_BTC", "0.05"))                      # Fallback static size in BTC if dynamic fails

# The experiment treats these venues as independent price-discovery sources. Polymarket is
# retained as an observed comparison feed, but it cannot nominate a trading signal.
MAJOR_DISCOVERY_VENUES = ("Binance", "Bybit", "OKX", "Hyperliquid")
ENTRY_CONSENSUS_STATUSES = ("HIGH_CONVICTION", "SUPER_CONVICTION")

# A feed must have delivered a valid quote recently before it can influence analytics or
# paper-trading decisions. This protects the experiment from stale-book false positives.
STALE_FEED_SECONDS = 3.0

# A spread closure is not automatically a Lighter catch-up. These values only define the
# observed event window; the analyzer records the reason the spread closed separately.
EVENT_RESOLUTION_LAG_USD = 1.5
MAX_EVENT_OBSERVATION_SECONDS = 20.0

# Learn structural contract basis only while markets are quiet. The basis is frozen during
# a breakout or active observation so it cannot absorb the lead-lag move under test.
BASIS_UPDATE_MAX_VELOCITY_USD = 0.5
BASIS_EMA_ALPHA_PER_SECOND = 0.02

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SQLITE_PATH = os.path.join(PROJECT_ROOT, "data", "lead_lag_dev.db")
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", DEFAULT_SQLITE_PATH)
FALLBACK_TO_SQLITE = os.getenv("FALLBACK_TO_SQLITE", "true").strip().lower() not in {"0", "false", "no"}

# Derived experiment state is persisted to PostgreSQL or local SQLite for development.
# The application never writes raw WebSocket messages, order-book depth, or every incoming quote to the database.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://lead_lag:lead_lag_dev_only@127.0.0.1:5432/lead_lag",
)
POSTGRES_REQUIRED = os.getenv("POSTGRES_REQUIRED", "true").strip().lower() not in {"0", "false", "no"}
POSTGRES_QUEUE_SIZE = int(os.getenv("POSTGRES_QUEUE_SIZE", "5000"))
POSTGRES_CHART_RETENTION = int(os.getenv("POSTGRES_CHART_RETENTION", "50000"))
PERSISTED_CHART_SAMPLE_INTERVAL_SECONDS = float(
    os.getenv("PERSISTED_CHART_SAMPLE_INTERVAL_SECONDS", "1.0")
)
# Real mode displays exchange-reported account values, refreshed independently
# of the high-frequency market-data callbacks.
REAL_ACCOUNT_REFRESH_SECONDS = max(0.5, float(os.getenv("REAL_ACCOUNT_REFRESH_SECONDS", "2.0")))


# Fee Schedules
FEES = {
    "lighter": {
        "taker_pct": 0.000,
        "maker_pct": 0.000,
        "label": "0.000% (Zero Fee)",
    },
    "binance": {
        "taker_pct": 0.050,
        "maker_pct": 0.020,
        "label": "Taker 0.050% | Maker 0.020%",
    },
    "bybit": {
        "taker_pct": 0.055,
        "maker_pct": 0.020,
        "label": "Taker 0.055% | Maker 0.020%",
    },
    "okx": {
        "taker_pct": 0.050,
        "maker_pct": 0.020,
        "label": "Taker 0.050% | Maker 0.020%",
    },
    "hyperliquid": {
        "taker_pct": 0.045,
        "maker_pct": 0.015,
        "label": "Taker 0.045% | Maker 0.015%",
    },
    "polymarket": {
        "taker_pct": 0.040,
        "maker_pct": 0.0125,
        "label": "Taker 0.040% | Maker 0.0125%",
    },
}

# Major-venue dynamic discovery & consensus parameters
LEADER_EVALUATION_WINDOW_SEC = 2.0  # Window to measure breakout price innovation
MIN_CONSENSUS_VELOCITY_USD = 2.0    # Minimum move to qualify as dynamic breakout leader
MIN_CONSENSUS_AGREEMENT = 3         # Minimum major venues agreeing on move direction
SUPER_CONVICTION_THRESHOLD = 4      # All four major venues moving together

# History buffer size for rolling charts and metrics
MAX_HISTORY_POINTS = 300
CHART_SAMPLE_INTERVAL_SECONDS = float(os.getenv("CHART_SAMPLE_INTERVAL_SECONDS", "0.25"))
MAX_CLOSED_TRADES_HISTORY = 500
MAX_REPRICING_EVENTS_HISTORY = 500
