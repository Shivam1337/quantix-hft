import pytest
from app.services.telemetry import telemetry


@pytest.mark.asyncio
async def test_websocket_throughput_endpoint(client):
    response = await client.get("/api/v1/telemetry/websocket-throughput")
    assert response.status_code == 200
    data = response.json()

    assert "venues" in data
    assert set(data["venues"]) == {"hyperliquid", "aevo", "lighter"}

    assert "current_rates" in data
    assert "total_processed" in data
    assert "history" in data
    assert len(data["history"]) >= 30

    first_entry = data["history"][0]
    assert "minute" in first_entry
    assert "timestamp" in first_entry
    assert "counts" in first_entry
    assert "total" in first_entry

    assert "rest_current_rates" in data
    assert "rest_total_processed" in data
    assert "rest_history" in data
    assert "rest_endpoints" in data

    before_hl = data["total_processed"]["hyperliquid"]
    telemetry.record_message("hyperliquid", 5)
    telemetry.record_rest_call("hyperliquid", "POST", "/info", 200)

    updated_resp = await client.get("/api/v1/telemetry/websocket-throughput")
    assert updated_resp.status_code == 200
    updated_data = updated_resp.json()
    assert updated_data["total_processed"]["hyperliquid"] == before_hl + 5
    assert updated_data["rest_total_processed"]["hyperliquid"] >= 1
    assert any(ep["endpoint"] == "/info" for ep in updated_data["rest_endpoints"]["hyperliquid"])
