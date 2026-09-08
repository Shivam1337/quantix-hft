import pytest
from datetime import datetime, timezone, timedelta
from app.models import FundingSnapshot, Position, TradeLog
from sqlalchemy import select


@pytest.mark.asyncio
async def test_simulation_account_and_reset(client):
    # Check initial account
    account_res = await client.get("/api/v1/simulation/account")
    assert account_res.status_code == 200
    acc = account_res.json()
    assert acc["initial_balance"] == 10_000.0
    assert acc["current_balance"] == 10_000.0
    assert acc["allocated_balance"] == 0.0

    # Open a test position to simulate trades
    radar = await client.get("/api/v1/opportunities?refresh=true")
    opp = radar.json()[0]
    opened = await client.post(
        "/api/v1/positions/open",
        json={"opportunity_id": opp["id"], "capital_usd": 2_000, "paper": True},
    )
    assert opened.status_code == 201

    positions_res = await client.get("/api/v1/positions")
    assert len(positions_res.json()) == 1

    # Call reset endpoint
    reset_res = await client.post("/api/v1/simulation/reset")
    assert reset_res.status_code == 200
    reset_data = reset_res.json()
    assert reset_data["status"] == "ok"
    assert reset_data["account"]["current_balance"] == 10_000.0
    assert reset_data["account"]["allocated_balance"] == 0.0

    # Positions and logs should be wiped
    positions_after = await client.get("/api/v1/positions")
    assert len(positions_after.json()) == 0
    logs_after = await client.get("/api/v1/logs")
    assert len(logs_after.json()) == 0


@pytest.mark.asyncio
async def test_autonomous_simulation_sizing_and_reasoning(client):
    # Refresh market opportunities
    await client.get("/api/v1/opportunities?refresh=true")
    
    # Run orchestrator cycle which evaluates and auto-trades
    # Retrieve app from client
    app = client._transport.app
    opportunities = await app.state.market.list_opportunities()
    assert len(opportunities) > 0

    # Trigger autonomous trade
    await app.state.simulation.evaluate_and_trade(opportunities)

    # Check that a position was automatically opened
    open_positions = await app.state.positions.list_positions(active_only=True)
    assert len(open_positions) == 1
    pos = open_positions[0]
    
    # System should divide account balance in half and use it on each leg
    account = await app.state.simulation.get_account()
    expected_leg_size = account.initial_balance / 2.0  # 5,000.0
    assert pos.leg_size_usd == pytest.approx(expected_leg_size)
    assert pos.open_reason is not None
    assert "Auto-opened" in pos.open_reason
    assert "$5,000.00/leg" in pos.open_reason or f"${expected_leg_size:,.2f}" in pos.open_reason


@pytest.mark.asyncio
async def test_3day_historical_funding_consideration(client):
    app = client._transport.app
    now = datetime.now(timezone.utc)
    
    # Seed historical snapshots over 3 days
    async with app.state.session_factory() as session:
        for day in range(3):
            obs = now - timedelta(days=day, hours=1)
            session.add(
                FundingSnapshot(
                    venue="hyperliquid",
                    symbol="BTC-PERP",
                    funding_rate=0.0005,
                    mark_price=65000,
                    open_interest=20_000_000,
                    bid=64990,
                    ask=65010,
                    observed_at=obs,
                )
            )
            session.add(
                FundingSnapshot(
                    venue="lighter",
                    symbol="BTC-PERP",
                    funding_rate=0.0001,
                    mark_price=65000,
                    open_interest=20_000_000,
                    bid=64990,
                    ask=65010,
                    observed_at=obs,
                )
            )
        await session.commit()

    # Ingest and verify historical fields are calculated
    opps = await app.state.market.refresh()
    btc_opp = next((o for o in opps if o.symbol == "BTC-PERP"), None)
    assert btc_opp is not None
    assert btc_opp.historical_snapshots_count > 0
    assert btc_opp.historical_3d_apr_pct is not None
