import pytest


@pytest.mark.asyncio
async def test_exchanges_api_and_funding_history(client):
    response = await client.get("/api/v1/exchanges")
    assert response.status_code == 200
    exchanges = response.json()
    assert len(exchanges) == 3

    venue_ids = {item["id"] for item in exchanges}
    assert venue_ids == {"hyperliquid", "aevo", "lighter"}

    for ex in exchanges:
        assert ex["status"] in ("active", "standby")
        assert len(ex["symbols"]) >= 2
        assert "BTC-PERP" in ex["symbols"]
        assert len(ex["markets"]) >= 1

    first_market = exchanges[0]["markets"][0]
    assert "funding_rate" in first_market
    assert "mark_price" in first_market
    assert first_market["mark_price"] > 0

    history_resp = await client.get(
        f"/api/v1/funding-history?venue={exchanges[0]['id']}&symbol={first_market['symbol']}"
    )
    assert history_resp.status_code == 200
    history = history_resp.json()
    assert isinstance(history, list)
