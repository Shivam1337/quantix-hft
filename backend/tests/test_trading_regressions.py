from dataclasses import replace
from datetime import timedelta

import pytest
from app.models import FundingPayment, FundingSettlement, Position, SimulationAccount
from sqlalchemy import select


def market_only_opportunity(values):
    return next(
        item
        for item in values
        if item.long_order_type == "market" and item.short_order_type == "market"
    )


@pytest.mark.asyncio
async def test_settings_propagate_to_worker_risk_and_disable_auto_unwind(client):
    app = client._transport.app
    opportunity = market_only_opportunity(
        await app.state.market.list_opportunities(refresh=True)
    )
    position = await app.state.positions.open_position(opportunity, 1_000, paper=True)
    breached = replace(opportunity, basis_bps=500)

    updated = await client.patch(
        "/api/v1/settings", json={"basis_threshold_bps": 40, "auto_unwind": False}
    )
    assert updated.status_code == 200
    assert await app.state.positions.evaluate_risk([breached]) == []
    assert (await app.state.positions.get_position(position.id)).status == "open"

    await client.patch("/api/v1/settings", json={"auto_unwind": True})
    assert position.id in await app.state.positions.evaluate_risk([breached])
    assert (await app.state.positions.get_position(position.id)).status == "closed"


@pytest.mark.asyncio
async def test_simulation_exit_path_respects_disabled_auto_unwind(client):
    app = client._transport.app
    opportunity = market_only_opportunity(
        await app.state.market.list_opportunities(refresh=True)
    )
    position = await app.state.positions.open_position(opportunity, 1_000, paper=True)
    await client.patch("/api/v1/settings", json={"auto_unwind": False})
    async with app.state.session_factory() as session:
        stored = await session.get(Position, position.id)
        stored.negative_hours = 2
        stored.last_net_apr_pct = -1
        await session.commit()

    await app.state.simulation.evaluate_and_trade([replace(opportunity, net_apr_pct=-1)])
    assert (await app.state.positions.get_position(position.id)).status == "open"


@pytest.mark.asyncio
async def test_small_loss_still_allows_entry_under_explicit_policy(client):
    app = client._transport.app
    await app.state.simulation.get_account()
    async with app.state.session_factory() as session:
        stored = await session.get(SimulationAccount, 1)
        stored.current_balance = 999.99
        stored.total_realized_pnl = -0.01
        await session.commit()
    opportunities = await app.state.market.list_opportunities(refresh=True)

    await app.state.simulation.evaluate_and_trade(opportunities)
    positions = await app.state.positions.list_positions(active_only=True)
    assert len(positions) == 1


@pytest.mark.asyncio
async def test_position_pnl_distinguishes_gross_paid_fees_and_close_estimate(client):
    radar = await client.get("/api/v1/opportunities?refresh=true")
    opportunity = radar.json()[0]
    opened = await client.post(
        "/api/v1/positions/open",
        json={"opportunity_id": opportunity["id"], "capital_usd": 1_000, "paper": True},
    )
    value = opened.json()
    assert value["gross_pnl_usd"] == pytest.approx(
        value["funding_pnl_usd"] + value["basis_pnl_usd"]
    )
    assert value["net_pnl_usd"] == pytest.approx(
        value["gross_pnl_usd"] - value["paid_fees_usd"]
    )
    assert value["estimated_net_pnl_if_closed_usd"] == pytest.approx(
        value["net_pnl_usd"] - value["estimated_close_fee_usd"]
    )


@pytest.mark.asyncio
async def test_delayed_funding_is_reconciled_once_and_capped_at_close(client):
    app = client._transport.app
    opportunity = market_only_opportunity(
        await app.state.market.list_opportunities(refresh=True)
    )
    position = await app.state.positions.open_position(opportunity, 1_000, paper=True)
    closed = await app.state.positions.close_position_with_market(
        position.id, opportunity, "regression close"
    )
    cycle = closed.last_funding_cycle + timedelta(hours=1)
    async with app.state.session_factory() as session:
        stored = await session.get(Position, position.id)
        stored.closed_at = cycle + timedelta(minutes=30)
        await session.commit()

    await app.state.simulation.reconcile_risk_closures([position.id])
    before = (await app.state.simulation.get_account()).current_balance
    async with app.state.session_factory() as session:
        session.add_all(
            [
                FundingSettlement(
                    venue=closed.long_venue,
                    symbol=closed.symbol,
                    funding_rate=0.0001,
                    funding_rate_native=0.0001,
                    funding_interval_hours=1,
                    funding_cycle_at=cycle,
                    settled_at=cycle + timedelta(minutes=5),
                    source="delayed-test",
                ),
                FundingSettlement(
                    venue=closed.short_venue,
                    symbol=closed.symbol,
                    funding_rate=0.0002,
                    funding_rate_native=0.0002,
                    funding_interval_hours=1,
                    funding_cycle_at=cycle,
                    settled_at=cycle + timedelta(minutes=5),
                    source="delayed-test",
                ),
            ]
        )
        await session.commit()

    await app.state.simulation.reconcile_risk_closures([position.id])
    after = (await app.state.simulation.get_account()).current_balance
    assert after > before
    await app.state.simulation.reconcile_risk_closures([position.id])
    assert (await app.state.simulation.get_account()).current_balance == pytest.approx(after)
    async with app.state.session_factory() as session:
        payments = list((await session.execute(select(FundingPayment))).scalars())
    assert len(payments) == 1

    late_cycle = cycle + timedelta(hours=1)
    async with app.state.session_factory() as session:
        session.add_all(
            [
                FundingSettlement(
                    venue=closed.long_venue,
                    symbol=closed.symbol,
                    funding_rate=0.0,
                    funding_rate_native=0.0,
                    funding_interval_hours=1,
                    funding_cycle_at=late_cycle,
                    settled_at=late_cycle,
                    source="after-close-test",
                ),
                FundingSettlement(
                    venue=closed.short_venue,
                    symbol=closed.symbol,
                    funding_rate=1.0,
                    funding_rate_native=1.0,
                    funding_interval_hours=1,
                    funding_cycle_at=late_cycle,
                    settled_at=late_cycle,
                    source="after-close-test",
                ),
            ]
        )
        await session.commit()
    await app.state.simulation.reconcile_risk_closures([position.id])
    assert (await app.state.simulation.get_account()).current_balance == pytest.approx(after)
