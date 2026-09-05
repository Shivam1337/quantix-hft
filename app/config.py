import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database Configuration (Defaults to PostgreSQL, can connect locally or via docker)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://poly_user:poly_secret_password@localhost:5432/polymarket_sim"
    )
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "poly_user")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "poly_secret_password")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "polymarket_sim")

    # Simulation Portfolio Constraints ($50 virtual capital)
    INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "50.0"))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.50")) # 50% max per position ($25)
    ARB_SPREAD_THRESHOLD: float = float(os.getenv("ARB_SPREAD_THRESHOLD", "0.015")) # 1.5% min net spread
    GAS_FEE_USD: float = float(os.getenv("GAS_FEE_USD", "0.005")) # Polygon L2 gas cost
    SIMULATED_SLIPPAGE: float = float(os.getenv("SIMULATED_SLIPPAGE", "0.002"))

    # Market Scanning Settings
    GAMMA_API_BASE: str = "https://gamma-api.polymarket.com"
    CLOB_API_BASE: str = "https://clob.polymarket.com"
    CLOB_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    MONITORED_EVENTS_LIMIT: int = int(os.getenv("MONITORED_EVENTS_LIMIT", "12"))
    POLL_INTERVAL_SECONDS: float = float(os.getenv("POLL_INTERVAL_SECONDS", "3.0"))

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
