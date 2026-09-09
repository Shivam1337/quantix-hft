import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from app.config import Settings
from app.domain.types import FundingSettlementData, MarketSnapshotData
from app.main import create_app
from httpx import ASGITransport, AsyncClient


class FixtureExchangeService:
    def __init__(self, symbols: list[str]):
        observed_at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        self.rates = {
            "hyperliquid": {"BTC-PERP": 0.00082, "ETH-PERP": 0.00062},
            "aevo": {"BTC-PERP": 0.00018, "ETH-PERP": 0.00024},
            "lighter": {"BTC-PERP": 0.000012, "ETH-PERP": -0.00008},
        }
        self.snapshots = [
            MarketSnapshotData(
                venue=venue,
                symbol=symbol,
                mark_price=65_000 if symbol == "BTC-PERP" else 3_200,
                open_interest=28_000_000 if symbol == "BTC-PERP" else 12_000_000,
                bid=64_999 if symbol == "BTC-PERP" else 3_199,
                ask=65_001 if symbol == "BTC-PERP" else 3_201,
                observed_at=observed_at,
            )
            for symbol in symbols
            for venue in ("hyperliquid", "aevo", "lighter")
        ]

    async def fetch_all(self) -> list[MarketSnapshotData]:
        return list(self.snapshots)

    async def fetch_funding_history(self, start_time, end_time):
        rows = []
        for hours_ago in range(1, 9):
            cycle = self.snapshots[0].observed_at - timedelta(hours=hours_ago)
            if cycle < start_time or cycle > end_time:
                continue
            for snapshot in self.snapshots:
                rate = self.rates[snapshot.venue].get(snapshot.symbol, 0.0001)
                rows.append(
                    FundingSettlementData(
                        venue=snapshot.venue,
                        symbol=snapshot.symbol,
                        funding_rate=rate,
                        funding_rate_native=rate,
                        funding_interval_hours=1.0,
                        funding_cycle_at=cycle,
                        settled_at=cycle,
                    )
                )
        return rows

    async def stream_all(self, rest_fallback_seconds: int = 120):
        while True:
            for snapshot in self.snapshots:
                yield snapshot
            await asyncio.sleep(rest_fallback_seconds)


@pytest.fixture
async def client(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    settings = Settings(
        database_url=database_url,
        redis_url="memory://",
        enable_scheduler=False,
        symbols=["BTC-PERP", "ETH-PERP"],
    )
    app = create_app(settings, exchange_service=FixtureExchangeService(settings.symbols))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
            yield value
