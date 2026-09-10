from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "Funding Arbitrage Platform"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://arbitrage:arbitrage@postgres:5432/arbitrage"
    redis_url: str = "redis://redis:6379/0"
    enable_scheduler: bool = True
    enable_exchange_websockets: bool = True
    market_poll_seconds: int = Field(default=30, ge=5, le=3600)
    websocket_batch_ms: int = Field(default=500, ge=50, le=5000)
    rest_fallback_seconds: int = Field(default=120, ge=15, le=3600)
    aevo_refresh_interval_seconds: int = Field(default=300, ge=60, le=3600)
    aevo_request_interval_seconds: float = Field(default=1.0, ge=0.1, le=10)
    aevo_max_concurrent_requests: int = Field(default=2, ge=1, le=10)
    symbols: list[str] = [
        "BTC-PERP",
        "ETH-PERP",
        "SOL-PERP",
        "ARB-PERP",
        "DOGE-PERP",
        "AVAX-PERP",
        "LINK-PERP",
        "SUI-PERP",
        "OP-PERP",
        "NEAR-PERP",
        "AAVE-PERP",
        "BNB-PERP",
        "TIA-PERP",
        "PENDLE-PERP",
        "JUP-PERP",
        "INJ-PERP",
        "ENA-PERP",
        "CRV-PERP",
        "LDO-PERP",
        "LTC-PERP",
        "POL-PERP",
        "APT-PERP",
        "DYDX-PERP",
        "UNI-PERP",
        "XRP-PERP",
        "WLD-PERP",
        "TAO-PERP",
        "ONDO-PERP",
        "HYPE-PERP",
        "WIF-PERP",
    ]
    default_min_apr: float = 10.0
    default_min_open_interest: float = 100_000.0
    basis_threshold_bps: float = 200.0
    entry_min_history_snapshots: int = Field(default=6, ge=2, le=100)
    entry_min_spread_stability_pct: float = Field(default=60.0, ge=0, le=100)
    entry_expected_holding_hours: float = Field(default=24.0, gt=0, le=8_760)
    capacity_fraction: float = Field(default=0.05, gt=0, le=1)
    auto_unwind: bool = True
    negative_hours_to_unwind: int = Field(default=2, ge=1, le=168)
    live_trading_enabled: bool = False
    simulation_initial_balance_usd: float = Field(default=1_000.0, gt=0)
    simulation_min_capital_usd: float = Field(default=100.0, gt=0)
    simulation_max_drawdown_pct: float = Field(default=25.0, ge=0, le=100)
    simulation_leverage: float = Field(default=3.0, ge=1, le=100)
    paper_slippage_bps: float = Field(default=1.0, ge=0, le=100)
    paper_post_only_fill_ratio: float = Field(default=0.90, gt=0, le=1)
    alert_webhook_url: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return ["http://localhost:5173", "http://localhost:8080"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
