import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.fees import FeeSchedule
from app.domain.types import MarketSnapshotData
from app.exchanges.base import ExchangeAdapter
from app.exchanges.service import ExchangeService
from app.services.historical_funding import HistoricalSpreadStats


def snapshot(venue: str) -> MarketSnapshotData:
    return MarketSnapshotData(
        venue=venue,
        symbol="BTC-PERP",
        mark_price=65_000,
        open_interest=1_000_000,
        bid=64_999,
        ask=65_001,
        observed_at=datetime.now(timezone.utc),
    )


def stat(
    long_rate: float,
    short_rate: float,
    long_venue: str = "lighter",
    short_venue: str = "hyperliquid",
    count: int = 6,
) -> HistoricalSpreadStats:
    latest = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    gross = short_rate - long_rate
    return HistoricalSpreadStats(
        long_venue=long_venue,
        short_venue=short_venue,
        long_avg_rate=long_rate,
        short_avg_rate=short_rate,
        historical_gross_hourly=gross,
        historical_net_apr_pct=gross * 24 * 365 * 100,
        snapshot_count=count,
        spread_stability_pct=100,
        oldest_cycle=latest - timedelta(hours=count - 1),
        latest_cycle=latest,
    )


def confirmed_stats() -> dict:
    rates = {"hyperliquid": 0.0008, "aevo": 0.0002, "lighter": -0.0002}
    result = {}
    venues = list(rates)
    for index, first in enumerate(venues):
        for second in venues[index + 1 :]:
            long_venue, short_venue = sorted((first, second), key=lambda venue: rates[venue])
            result[("BTC-PERP", long_venue, short_venue)] = stat(
                rates[long_venue], rates[short_venue], long_venue, short_venue
            )
    return result


class StreamingFixture(ExchangeAdapter):
    name = "lighter"

    async def fetch_markets(self, symbols: list[str]) -> list[MarketSnapshotData]:
        return [snapshot(self.name)]

    async def stream_markets(self, symbols: list[str]):
        while True:
            yield snapshot(self.name)
            await asyncio.sleep(60)


def test_calculator_uses_confirmed_history_for_direction_and_fee_drag():
    values = calculate_opportunities(
        [snapshot("hyperliquid"), snapshot("aevo"), snapshot("lighter")],
        CalculatorConfig(),
        confirmed_stats(),
    )
    assert len(values) == 3
    best = values[0]
    assert best.long_venue == "lighter"
    assert best.short_venue == "hyperliquid"
    assert best.gross_hourly_rate == 0.001
    assert best.net_hourly_rate < best.gross_hourly_rate
    assert best.net_apr_pct > 0
    assert best.entry_fee_bps == 1.5
    assert best.funding_rate_source == "confirmed_history"


def test_calculator_returns_no_opportunity_without_confirmed_history():
    assert calculate_opportunities(
        [snapshot("hyperliquid"), snapshot("lighter")], CalculatorConfig()
    ) == []


def test_aevo_uses_standard_perpetual_taker_fee_for_market_orders():
    values = calculate_opportunities(
        [snapshot("aevo"), snapshot("lighter")],
        CalculatorConfig(),
        {("BTC-PERP", "lighter", "aevo"): stat(-0.0002, 0.0002, "lighter", "aevo")},
    )
    value = values[0]
    assert value.long_venue == "lighter"
    assert value.short_venue == "aevo"
    assert value.entry_fee_bps == 8.0
    assert value.round_trip_fee_bps == 16.0


@pytest.mark.asyncio
async def test_exchange_service_streams_updates_without_poll_loop():
    service = ExchangeService([StreamingFixture()], ["BTC-PERP"])
    stream = service.stream_all(rest_fallback_seconds=120)
    try:
        value = await anext(stream)
        assert value.venue == "lighter"
        assert not hasattr(value, "funding_rate")
    finally:
        await stream.aclose()


def test_live_snapshot_contains_only_execution_data():
    value = snapshot("lighter")
    assert value.mark_price > 0
    assert value.bid > 0
    assert value.ask > 0
    assert not hasattr(value, "funding_rate")


@pytest.mark.asyncio
async def test_market_engine_replaces_live_liquidity_snapshot(tmp_path):
    from app.cache import Cache
    from app.models import Base
    from app.services.market import MarketEngine
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'market_test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    service = StreamingFixture()
    market = MarketEngine(session_factory, service, Cache(None), CalculatorConfig())
    await market.ingest([snapshot("lighter")])
    assert market.latest_snapshots[("lighter", "BTC-PERP")].mark_price == 65_000
    cached = await market.cache.get_json("market:snapshots")
    assert cached[0].keys() == {
        "venue",
        "symbol",
        "mark_price",
        "open_interest",
        "bid",
        "ask",
        "observed_at",
    }
    updated = MarketSnapshotData(
        venue="lighter",
        symbol="BTC-PERP",
        mark_price=65_100,
        open_interest=1_000_000,
        bid=65_099,
        ask=65_101,
        observed_at=datetime.now(timezone.utc),
    )
    await market.ingest([updated])
    assert market.latest_snapshots[("lighter", "BTC-PERP")].mark_price == 65_100
    restored = MarketEngine(session_factory, service, market.cache, CalculatorConfig())
    assert await restored.load_cached_snapshots()
    assert restored.latest_snapshots[("lighter", "BTC-PERP")].mark_price == 65_100
    await engine.dispose()


def test_calculator_does_not_create_opportunities_for_one_venue():
    assert calculate_opportunities(
        [snapshot("hyperliquid")],
        CalculatorConfig(fee_schedule=FeeSchedule(venues={"hyperliquid": {}})),
        {},
    ) == []
