from dataclasses import replace
from datetime import timedelta

import pytest
from app.models import FundingPayment, Position


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

    # Autonomous entry uses the fixture's exchange-confirmed cycles.
    opportunities = await app.state.market.refresh()

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
async def test_unconfirmed_negative_tick_does_not_churn_position(client):
    app = client._transport.app
    opportunities = await app.state.market.list_opportunities(refresh=True)
    initial = opportunities[0]
    position = await app.state.positions.open_position(initial, 1_000, paper=True)
    negative_tick = replace(
        initial,
        net_apr_pct=-1,
        basis_bps=0,
        historical_3d_apr_pct=-1,
    )

    await app.state.positions.evaluate_risk([negative_tick])
    await app.state.simulation.evaluate_and_trade([negative_tick])

    active = await app.state.positions.list_positions(active_only=True)
    assert len(active) == 1
    assert active[0].id == position.id
    assert active[0].negative_hours == 0

    logs = await client.get("/api/v1/logs")
    assert len(logs.json()) == 2


@pytest.mark.asyncio
async def test_confirmed_negative_funding_closes_after_two_cycles(client):
    app = client._transport.app
    opportunities = await app.state.market.list_opportunities(refresh=True)
    position = await app.state.positions.open_position(opportunities[0], 1_000, paper=True)

    async with app.state.session_factory() as session:
        stored = await session.get(Position, position.id)
        cycle = stored.last_funding_cycle
        for offset, net_payment in ((0, -0.1), (1, -0.1), (2, 0.1)):
            cycle_at = cycle - timedelta(hours=offset)
            long_rate = 0.0001
            short_rate = 0.0 if net_payment < 0 else 0.0002
            session.add(
                FundingPayment(
                    position_id=position.id,
                    symbol=position.symbol,
                    long_venue=position.long_venue,
                    short_venue=position.short_venue,
                    long_rate=long_rate,
                    short_rate=short_rate,
                    long_payment_usd=-long_rate * position.leg_size_usd,
                    short_payment_usd=short_rate * position.leg_size_usd,
                    net_payment_usd=net_payment,
                    cycle_at=cycle_at,
                    settlement_type="confirmed",
                    rate_source="exchange_history",
                )
            )
        await session.commit()

    closed_ids = await app.state.positions.evaluate_risk(opportunities)
    assert position.id in closed_ids
    stored = await app.state.positions.get_position(position.id)
    assert stored.status == "closed"
    assert stored.close_reason == "negative net APR persisted for 2 funding hours"


@pytest.mark.asyncio
async def test_risk_closure_reconciles_paper_account(client):
    app = client._transport.app
    opportunities = await app.state.market.list_opportunities(refresh=True)
    position = await app.state.positions.open_position(opportunities[0], 1_000, paper=True)

    async with app.state.session_factory() as session:
        stored = await session.get(Position, position.id)
        stored.status = "closed"
        stored.funding_pnl_usd = 2
        stored.basis_pnl_usd = 3
        stored.fees_usd = 1
        await session.commit()

    await app.state.simulation.reconcile_risk_closures([position.id])

    account = await app.state.simulation.get_account()
    assert account.current_balance == pytest.approx(10_004)
    assert account.total_realized_pnl == pytest.approx(4)
    assert account.allocated_balance == pytest.approx(0)


@pytest.mark.asyncio
async def test_3day_historical_funding_consideration(client):
    app = client._transport.app
    # Confirmed settlement history is separate from live market snapshots.
    opps = await app.state.market.refresh()
    btc_opp = next((o for o in opps if o.symbol == "BTC-PERP"), None)
    assert btc_opp is not None
    assert btc_opp.historical_snapshots_count > 0
    assert btc_opp.historical_3d_apr_pct is not None
