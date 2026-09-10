import pytest


@pytest.mark.asyncio
async def test_health_and_opportunity_radar(client):
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["data_mode"] == "historical-confirmed-rates"

    response = await client.get("/api/v1/opportunities?refresh=true")
    assert response.status_code == 200
    assert len(response.json()) == 6
    assert response.json()[0]["net_apr_pct"] > 10
    assert all(item["funding_rate_source"] == "confirmed_history" for item in response.json())
    history = await client.get("/api/v1/funding-history?symbol=BTC-PERP")
    assert history.status_code == 200
    assert len(history.json()) >= 6
    assert all(item["funding_interval_hours"] == 1 for item in history.json())
    await client.get("/api/v1/opportunities?refresh=true")
    assert len((await client.get("/api/v1/funding-history?symbol=BTC-PERP")).json()) >= 6


@pytest.mark.asyncio
async def test_simulator_open_close_and_logs(client):
    radar = await client.get("/api/v1/opportunities?refresh=true")
    opportunity = radar.json()[0]
    close_opportunity = next(
        item
        for item in radar.json()
        if item["long_order_type"] == "market" and item["short_order_type"] == "market"
    )
    simulation = await client.post(
        "/api/v1/simulator",
        json={"opportunity_id": opportunity["id"], "capital_usd": 50, "holding_days": 30},
    )
    assert simulation.status_code == 200
    simulation_data = simulation.json()
    assert simulation_data["leverage"] == 3
    assert simulation_data["leg_margin_usd"] == pytest.approx(25)
    assert simulation_data["leg_notional_usd"] == pytest.approx(75)
    assert simulation_data["position_notional_usd"] == pytest.approx(150)
    assert simulation_data["expected_fill_ratio"] == pytest.approx(0.9)
    assert simulation_data["expected_filled_leg_notional_usd"] == pytest.approx(67.5)
    assert simulation_data["estimated_entry_fees_usd"] == pytest.approx(
        67.5 * opportunity["entry_fee_bps"] / 10_000
    )
    assert simulation_data["estimated_exit_fees_usd"] == pytest.approx(
        67.5 * opportunity["exit_fee_bps"] / 10_000
    )
    assert simulation_data["estimated_round_trip_fees_usd"] == pytest.approx(
        simulation_data["estimated_entry_fees_usd"] + simulation_data["estimated_exit_fees_usd"]
    )
    assert simulation_data["projected_net_profit_usd"] == pytest.approx(
        simulation_data["projected_period_funding_usd"]
        - simulation_data["estimated_round_trip_fees_usd"]
        - simulation_data["estimated_slippage_usd"]
    )

    opened = await client.post(
        "/api/v1/positions/open",
        json={
            "opportunity_id": close_opportunity["id"],
            "capital_usd": 1_000,
            "paper": True,
        },
    )
    assert opened.status_code == 201
    position_id = opened.json()["id"]
    assert opened.json()["entry_fee_usd"] == pytest.approx(
        opened.json()["size_usd"] * close_opportunity["entry_fee_bps"] / 10_000
    )
    assert opened.json()["exit_fee_usd"] == 0
    assert opened.json()["fees_usd"] == opened.json()["entry_fee_usd"]
    active = await client.get("/api/v1/positions")
    assert len(active.json()) == 1

    closed = await client.post(f"/api/v1/positions/{position_id}/close", json={"reason": "test"})
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["exit_fee_usd"] == pytest.approx(
        opened.json()["size_usd"] * close_opportunity["exit_fee_bps"] / 10_000
    )
    assert closed.json()["fees_usd"] == pytest.approx(
        closed.json()["entry_fee_usd"] + closed.json()["exit_fee_usd"]
    )
    logs = await client.get("/api/v1/logs")
    assert logs.status_code == 200
    values = logs.json()
    assert len(values) == 4
    assert {value["phase"] for value in values} == {"open", "close"}
    assert all(value["symbol"] == close_opportunity["symbol"] for value in values)
    assert all(
        value["fee_usd"] == pytest.approx(value["size_usd"] * value["fee_bps"] / 10_000)
        for value in values
    )
    assert sum(value["fee_usd"] for value in values if value["phase"] == "open") == pytest.approx(
        closed.json()["entry_fee_usd"]
    )
    assert sum(value["fee_usd"] for value in values if value["phase"] == "close") == pytest.approx(
        closed.json()["exit_fee_usd"]
    )


@pytest.mark.asyncio
async def test_settings_update_changes_risk_configuration(client):
    updated = await client.patch(
        "/api/v1/settings",
        json={"min_apr": 10_000, "basis_threshold_bps": 40, "auto_unwind": False},
    )
    assert updated.status_code == 200
    assert updated.json()["min_apr"] == 10_000
    radar = await client.get("/api/v1/opportunities?refresh=true")
    assert radar.json() == []


@pytest.mark.asyncio
async def test_funding_payments_endpoint(client):
    res = await client.get("/api/v1/funding-payments")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
