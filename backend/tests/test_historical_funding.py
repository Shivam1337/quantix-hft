from datetime import datetime, timedelta, timezone

import pytest
from app.models import Base, FundingSettlement
from app.services.historical_funding import HistoricalFundingService
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def seed_rows(session, cycles, rates):
    for cycle in cycles:
        for venue, rate in rates.items():
            session.add(
                FundingSettlement(
                    venue=venue,
                    symbol="SOL-PERP",
                    funding_rate=rate(cycle) if callable(rate) else rate,
                    funding_rate_native=rate(cycle) if callable(rate) else rate,
                    funding_interval_hours=1.0,
                    funding_cycle_at=cycle,
                    settled_at=cycle + timedelta(minutes=1),
                    source="fixture_exchange_history",
                )
            )
    await session.commit()


@pytest.mark.asyncio
async def test_stats_use_median_of_aligned_confirmed_cycles(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycles = [now - timedelta(hours=offset) for offset in range(8, 0, -1)]
    async with sessions() as session:
        await seed_rows(
            session,
            cycles,
            {
                "hyperliquid": 0.0001,
                "lighter": lambda cycle: 0.0002 if cycle != cycles[2] else 0.01,
            },
        )

    service = HistoricalFundingService(sessions, min_cycles=6)
    stats = await service.get_historical_stats(now=now)
    value = stats[("SOL-PERP", "hyperliquid", "lighter")]
    assert value.snapshot_count == 8
    assert value.long_avg_rate == pytest.approx(0.0001)
    assert value.short_avg_rate == pytest.approx(0.0002)
    assert value.historical_gross_hourly == pytest.approx(0.0001)
    await engine.dispose()


@pytest.mark.asyncio
async def test_stats_reject_any_unaligned_cycle_gap(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gap.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cycles = [now - timedelta(hours=offset) for offset in range(8, 0, -1)]
    missing = cycles[2]
    async with sessions() as session:
        await seed_rows(
            session,
            cycles,
            {"hyperliquid": 0.0001, "lighter": 0.0002},
        )
        await session.execute(
            delete(FundingSettlement).where(
                FundingSettlement.venue == "lighter",
                FundingSettlement.symbol == "SOL-PERP",
                FundingSettlement.funding_cycle_at == missing,
            )
        )
        await session.commit()

    stats = await HistoricalFundingService(sessions, min_cycles=6).get_historical_stats(now=now)
    assert stats == {}
    await engine.dispose()
