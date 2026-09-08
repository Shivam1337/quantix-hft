from datetime import datetime, timezone

import pytest
from app.exchanges.aevo import AevoAdapter
from app.exchanges.lighter import LighterAdapter


class AevoFixtureAdapter(AevoAdapter):
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
