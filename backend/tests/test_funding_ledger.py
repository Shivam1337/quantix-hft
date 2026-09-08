from datetime import datetime, timedelta, timezone

import pytest
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.types import MarketSnapshotData
from app.models import Base, FundingSnapshot, Position
from app.services.funding_ledger import FundingLedger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def market_snapshot(venue: str, rate: float, cycle: datetime) -> MarketSnapshotData:
    return MarketSnapshotData(
        venue=venue,
        symbol="BTC-PERP",
        funding_rate=rate,
        mark_price=65_000,
        open_interest=1_000_000,
        bid=64_999,
        ask=65_001,
        observed_at=cycle,
        funding_rate_native=rate,
        funding_interval_hours=1,
        funding_cycle_at=cycle,
    )


@pytest.mark.asyncio
async def test_ledger_separates_settled_and_current_estimate(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cycle = datetime(2026, 9, 8, 11, tzinfo=timezone.utc)
    opportunities = calculate_opportunities(
        [market_snapshot("hyperliquid", 0.0001, cycle), market_snapshot("lighter", 0.0002, cycle)],
        CalculatorConfig(),
    )
    opportunity = opportunities[0]
    position = Position(
        id="ledger-position",
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        long_venue=opportunity.long_venue,
        short_venue=opportunity.short_venue,
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

    async with sessions() as session:
        session.add(position)
        for venue, rate in (("hyperliquid", 0.0001), ("lighter", 0.0002)):
            session.add(
                FundingSnapshot(
                    venue=venue,
                    symbol="BTC-PERP",
                    funding_rate=rate,
                    mark_price=65_000,
                    open_interest=1_000_000,
                    bid=64_999,
                    ask=65_001,
                    observed_at=cycle,
                    funding_cycle_at=cycle,
                )
            )
        await session.commit()

        await FundingLedger().mark_position(
            session,
            position,
            opportunity,
            cycle + timedelta(minutes=30),
        )
        await session.commit()

    assert position.settled_long_funding_pnl_usd == pytest.approx(-0.1)
    assert position.settled_short_funding_pnl_usd == pytest.approx(0.2)
    assert position.settled_funding_pnl_usd == pytest.approx(0.1)
    assert position.accrued_funding_pnl_usd == pytest.approx(0.05)
    assert position.funding_pnl_usd == pytest.approx(0.15)
    await engine.dispose()
