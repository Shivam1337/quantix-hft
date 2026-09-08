import asyncio
from datetime import datetime, timezone

import pytest
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.fees import FeeSchedule
from app.domain.types import MarketSnapshotData
from app.exchanges.base import ExchangeAdapter
from app.exchanges.service import ExchangeService


def snapshot(venue: str, rate: float) -> MarketSnapshotData:
    return MarketSnapshotData(
        venue=venue,
        symbol="BTC-PERP",
        funding_rate=rate,
        mark_price=65_000,
        open_interest=1_000_000,
        bid=64_999,
        ask=65_001,
        observed_at=datetime.now(timezone.utc),
        funding_rate_native=rate,
        funding_interval_hours=1,
        funding_cycle_at=datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0),
    )


class StreamingFixture(ExchangeAdapter):
    name = "lighter"

    async def fetch_markets(self, symbols: list[str]) -> list[MarketSnapshotData]:
        return [snapshot(self.name, 0.000012)]

    async def stream_markets(self, symbols: list[str]):
        while True:
            yield snapshot(self.name, 0.000012)
            await asyncio.sleep(60)


def test_calculator_normalizes_direction_and_fee_drag():
    values = calculate_opportunities(
        [snapshot("hyperliquid", 0.0008), snapshot("aevo", 0.0002), snapshot("lighter", -0.0002)],
        CalculatorConfig(),
    )
    assert len(values) == 3
    best = values[0]
    assert best.long_venue == "lighter"
    assert best.short_venue == "hyperliquid"
    assert best.gross_hourly_rate == 0.001
    assert best.net_hourly_rate < best.gross_hourly_rate
    assert best.net_apr_pct > 0
    assert best.entry_fee_bps == 1.5
    assert best.exit_fee_bps == 1.5
    assert best.round_trip_fee_bps == 3.0
    assert best.fee_breakeven_hours == 0.3


def test_calculator_does_not_create_opportunities_for_one_venue():
    assert calculate_opportunities(
        [snapshot("hyperliquid", 0.001)], CalculatorConfig(fee_schedule=FeeSchedule(venues={}))
    ) == []


def test_aevo_uses_standard_perpetual_taker_fee_for_market_orders():
    values = calculate_opportunities(
        [snapshot("aevo", 0.0002), snapshot("lighter", -0.0002)], CalculatorConfig()
    )
    value = values[0]
    assert value.long_venue == "lighter"
    assert value.short_venue == "aevo"
    assert value.entry_fee_bps == 8.0
    assert value.exit_fee_bps == 8.0
    assert value.round_trip_fee_bps == 16.0


@pytest.mark.asyncio
async def test_exchange_service_streams_updates_without_poll_loop():
    service = ExchangeService([StreamingFixture()], ["BTC-PERP"])
    stream = service.stream_all(rest_fallback_seconds=120)
    try:
        value = await anext(stream)
        assert value.venue == "lighter"
        assert value.symbol == "BTC-PERP"
    finally:
        await stream.aclose()


def test_snapshot_preserves_native_rate_and_cycle_metadata():
    value = snapshot("lighter", 0.000012)
    assert value.funding_rate_native == pytest.approx(0.000012)
    assert value.funding_interval_hours == 1
    assert value.funding_cycle_at is not None


@pytest.mark.asyncio
async def test_market_engine_stabilizes_funding_rate_within_same_cycle(tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.cache import Cache
    from app.models import Base
    from app.services.market import MarketEngine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'market_test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    cycle_time = datetime(2026, 9, 8, 16, 0, 0, tzinfo=timezone.utc)
    snap1 = MarketSnapshotData(
        venue="hyperliquid",
        symbol="BTC-PERP",
        funding_rate=0.0005,
        mark_price=65000,
        open_interest=1000000,
        bid=64999,
        ask=65001,
        observed_at=datetime(2026, 9, 8, 16, 5, 0, tzinfo=timezone.utc),
        funding_rate_native=0.0005,
        funding_interval_hours=1,
        funding_cycle_at=cycle_time,
    )
    snap_leg = MarketSnapshotData(
        venue="lighter",
        symbol="BTC-PERP",
        funding_rate=0.0001,
        mark_price=65010,
        open_interest=1000000,
        bid=65009,
        ask=65011,
        observed_at=datetime(2026, 9, 8, 16, 5, 0, tzinfo=timezone.utc),
        funding_rate_native=0.0001,
        funding_interval_hours=1,
        funding_cycle_at=cycle_time,
    )
    service = StreamingFixture()
    market = MarketEngine(session_factory, service, Cache(None), CalculatorConfig())
    await market.ingest([snap1, snap_leg])
    assert market.latest_snapshots[("hyperliquid", "BTC-PERP")].funding_rate == 0.0005

    # Sub-second tick in same cycle with jittered rate: rate is locked to cycle, price updates
    snap2 = MarketSnapshotData(
        venue="hyperliquid",
        symbol="BTC-PERP",
        funding_rate=0.00099,
        mark_price=65100,
        open_interest=1000000,
        bid=65099,
        ask=65101,
        observed_at=datetime(2026, 9, 8, 16, 5, 1, tzinfo=timezone.utc),
        funding_rate_native=0.00099,
        funding_interval_hours=1,
        funding_cycle_at=cycle_time,
    )
    await market.ingest([snap2])
    hl_snap = market.latest_snapshots[("hyperliquid", "BTC-PERP")]
    assert hl_snap.funding_rate == 0.0005
    assert hl_snap.mark_price == 65100

    # Next funding cycle (17:00): rate updates to the new cycle rate
    next_cycle = datetime(2026, 9, 8, 17, 0, 0, tzinfo=timezone.utc)
    snap3 = MarketSnapshotData(
        venue="hyperliquid",
        symbol="BTC-PERP",
        funding_rate=0.00075,
        mark_price=65200,
        open_interest=1000000,
        bid=65199,
        ask=65201,
        observed_at=datetime(2026, 9, 8, 17, 0, 1, tzinfo=timezone.utc),
        funding_rate_native=0.00075,
        funding_interval_hours=1,
        funding_cycle_at=next_cycle,
    )
    await market.ingest([snap3])
    assert market.latest_snapshots[("hyperliquid", "BTC-PERP")].funding_rate == 0.00075
    await engine.dispose()

