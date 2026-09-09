from datetime import datetime, timezone

import httpx
import pytest
from app.exchanges.aevo import AevoAdapter
from app.exchanges.base import ExchangeRateLimited, HttpExchangeAdapter
from app.exchanges.lighter import LighterAdapter
from app.services.telemetry import telemetry


class AevoFixtureAdapter(AevoAdapter):
    def __init__(self):
        super().__init__(request_interval_seconds=0)

    async def _request(self, method: str, path: str, **kwargs):
        if path.startswith("/instrument/"):
            return {
                "instrument_name": "BTC-PERP",
                "mark_price": "65000",
                "markets": {"total_oi": "20"},
                "best_bid": {"price": "64999"},
                "best_ask": {"price": "65001"},
            }
        return {"funding_rate": "0.000012", "next_epoch": "1788861600000000000"}


class AevoRateLimitFixtureAdapter(AevoAdapter):
    def __init__(self):
        super().__init__(request_interval_seconds=0)
        self.calls: list[str] = []

    async def _request(self, method: str, path: str, **kwargs):
        self.calls.append(path)
        if path.startswith("/instrument/"):
            return {
                "instrument_name": "BTC-PERP",
                "mark_price": "65000",
                "markets": {"total_oi": "20"},
                "best_bid": {"price": "64999"},
                "best_ask": {"price": "65001"},
            }
        raise ExchangeRateLimited("fixture rate limit", retry_after_seconds=300)


class ProbeAdapter(HttpExchangeAdapter):
    async def fetch_markets(self, symbols: list[str]):
        return []


class LighterFixtureAdapter(LighterAdapter):
    async def _request(self, method: str, path: str, **kwargs):
        if path.endswith("orderBookDetails"):
            return {
                "order_book_details": [
                    {
                        "symbol": "BTC",
                        "market_id": 1,
                        "mark_price": "65000",
                        "open_interest": "20",
                        "last_trade_price": "64998",
                    }
                ]
            }
        return {
            "fundings": [
                {"timestamp": 1788861600, "rate": "0.0012", "direction": "long"}
            ]
        }


@pytest.mark.asyncio
async def test_aevo_fetch_uses_real_instrument_and_hourly_funding_fields():
    value = (await AevoFixtureAdapter().fetch_markets(["BTC-PERP"]))[0]
    assert value.funding_rate == pytest.approx(0.000012)
    assert value.funding_rate_native == pytest.approx(0.000012)
    assert value.open_interest == pytest.approx(1_300_000)
    assert value.bid == 64_999
    assert value.ask == 65_001
    assert value.funding_interval_hours == 1


@pytest.mark.asyncio
async def test_aevo_rate_limit_sets_cooldown_and_prevents_retry_storm():
    adapter = AevoRateLimitFixtureAdapter()

    assert await adapter.fetch_markets(["BTC-PERP"]) == []
    assert await adapter.fetch_markets(["BTC-PERP"]) == []

    assert adapter.calls == ["/instrument/BTC-PERP", "/funding"]


@pytest.mark.asyncio
async def test_http_rate_limit_honors_retry_after_and_records_once():
    async def handler(request):
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"detail": "slow down"},
            request=request,
        )

    adapter = ProbeAdapter("aevo", "https://example.test")
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    endpoint_key = "GET /probe"
    before = telemetry._rest_endpoints.get("aevo", {}).get(endpoint_key, {}).get(
        "calls_total", 0
    )

    with pytest.raises(ExchangeRateLimited) as error:
        await adapter._request("GET", "/probe")

    after = telemetry._rest_endpoints["aevo"][endpoint_key]["calls_total"]
    assert error.value.retry_after_seconds == 7
    assert after - before == 1
    await adapter.aclose()


@pytest.mark.asyncio
async def test_lighter_percent_rate_is_normalized_to_hourly_decimal():
    value = (await LighterFixtureAdapter().fetch_markets(["BTC-PERP"]))[0]
    assert value.funding_rate_native == pytest.approx(0.0012)
    assert value.funding_rate == pytest.approx(0.000012)
    assert value.funding_cycle_at == datetime.fromtimestamp(1788861600, timezone.utc)
    assert value.open_interest == pytest.approx(1_300_000)


def test_lighter_market_stats_stream_preserves_native_percent_rate():
    value = LighterAdapter()._stream_snapshot(
        {
            "symbol": "BTC",
            "mark_price": "65000",
            "best_bid_price": "64999",
            "best_ask_price": "65001",
            "open_interest": "1300000",
            "funding_rate": "0.0012",
            "funding_timestamp": 1788861600000,
        },
        {"BTC-PERP"},
        1788861600000,
    )
    assert value is not None
    assert value.funding_rate == pytest.approx(0.000012)
    assert value.funding_rate_native == pytest.approx(0.0012)
