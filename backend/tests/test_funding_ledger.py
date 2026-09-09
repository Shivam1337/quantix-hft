from datetime import datetime, timedelta, timezone

import pytest
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.types import MarketSnapshotData
from app.models import Base, FundingPayment, FundingPendingCycle, FundingSettlement, Position
from app.services.funding_ledger import FundingLedger
from app.services.historical_funding import HistoricalSpreadStats
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def market_snapshot(venue: str, cycle: datetime) -> MarketSnapshotData:
    return MarketSnapshotData(
        venue=venue,
        symbol="BTC-PERP",
        mark_price=65_000,
        open_interest=1_000_000,
        bid=64_999,
        ask=65_001,
        observed_at=cycle,
    )


def opportunity(cycle: datetime):
    stat = HistoricalSpreadStats(
        long_venue="hyperliquid",
        short_venue="lighter",
        long_avg_rate=0.0001,
        short_avg_rate=0.0002,
        historical_gross_hourly=0.0001,
        historical_net_apr_pct=0.0001 * 24 * 365 * 100,
        snapshot_count=6,
        spread_stability_pct=100,
        oldest_cycle=cycle - timedelta(hours=5),
        latest_cycle=cycle,
    )
    return calculate_opportunities(
        [market_snapshot("hyperliquid", cycle), market_snapshot("lighter", cycle)],
        CalculatorConfig(),
        {("BTC-PERP", "hyperliquid", "lighter"): stat},
    )[0]


def make_position(value, cycle):
    return Position(
        id="ledger-position",
        opportunity_id=value.id,
        symbol=value.symbol,
        long_venue=value.long_venue,
        short_venue=value.short_venue,
        size_usd=1_000,
        leg_size_usd=1_000,
        long_entry_price=65_000,
        short_entry_price=65_000,
        entry_basis_bps=0,
        opened_at=cycle - timedelta(hours=1),
        updated_at=cycle - timedelta(hours=1),
        status="open",
        last_funding_cycle=cycle - timedelta(hours=1),
        accrual_started_at=cycle - timedelta(hours=1),
    )


async def settlement(venue, rate, cycle):
    return FundingSettlement(
        venue=venue,
        symbol="BTC-PERP",
        funding_rate=rate,
        funding_rate_native=rate,
        funding_interval_hours=1,
        funding_cycle_at=cycle,
        settled_at=cycle + timedelta(minutes=1),
        source="fixture_exchange_history",
    )


@pytest.mark.asyncio
async def test_ledger_uses_only_confirmed_settlements(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cycle = datetime(2026, 9, 8, 11, tzinfo=timezone.utc)
    value = opportunity(cycle)
    position = make_position(value, cycle)

    async with sessions() as session:
        session.add(position)
        session.add(await settlement("hyperliquid", 0.0001, cycle))
        session.add(await settlement("lighter", 0.0002, cycle))
        await session.commit()

        await FundingLedger().mark_position(session, position, value, cycle + timedelta(minutes=30))
        await session.commit()

    assert position.settled_long_funding_pnl_usd == pytest.approx(-0.1)
    assert position.settled_short_funding_pnl_usd == pytest.approx(0.2)
    assert position.settled_funding_pnl_usd == pytest.approx(0.1)
    assert position.accrued_funding_pnl_usd == pytest.approx(0.0)
    assert position.funding_pnl_usd == pytest.approx(0.1)
    await engine.dispose()


@pytest.mark.asyncio
async def test_missing_confirmation_is_pending_and_never_falls_back(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pending.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cycle = datetime(2026, 9, 8, 11, tzinfo=timezone.utc)
    value = opportunity(cycle)
    position = make_position(value, cycle)

    async with sessions() as session:
        session.add(position)
        session.add(await settlement("hyperliquid", 0.0001, cycle))
        await session.commit()
        await FundingLedger().mark_position(session, position, value, cycle + timedelta(minutes=30))
        await session.commit()
        pending = list((await session.execute(select(FundingPendingCycle))).scalars())
        payments = list((await session.execute(select(FundingPayment))).scalars())
        assert len(pending) == 1
        assert payments == []
        assert position.settled_funding_pnl_usd == 0

        session.add(await settlement("lighter", 0.0002, cycle))
        await FundingLedger().mark_position(session, position, value, cycle + timedelta(minutes=30))
        await session.commit()
        pending = list((await session.execute(select(FundingPendingCycle))).scalars())
        payments = list((await session.execute(select(FundingPayment))).scalars())

    assert pending == []
    assert len(payments) == 1
    assert payments[0].rate_source == "exchange_history"
    assert payments[0].settlement_type == "confirmed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_simulated_payment_with_history_label_is_not_trusted(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'contaminated.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cycle = datetime(2026, 9, 8, 11, tzinfo=timezone.utc)
    value = opportunity(cycle)
    position = make_position(value, cycle)

    async with sessions() as session:
        session.add(position)
        session.add(
            FundingPayment(
                position_id=position.id,
                symbol=position.symbol,
                long_venue=position.long_venue,
                short_venue=position.short_venue,
                long_rate=0.0001,
                short_rate=0.0002,
                long_payment_usd=-0.1,
                short_payment_usd=0.2,
                net_payment_usd=0.1,
                cycle_at=cycle,
                settlement_type="simulated",
                rate_source="exchange_history",
            )
        )
        await session.commit()

        await FundingLedger().mark_position(session, position, value, cycle + timedelta(minutes=30))
        await session.commit()
        pending = list((await session.execute(select(FundingPendingCycle))).scalars())

    assert len(pending) == 1
    assert position.settled_funding_pnl_usd == 0
    await engine.dispose()
