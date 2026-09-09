import json
from datetime import datetime, timezone

from websockets import connect

from app.domain.types import FundingSettlementData, MarketSnapshotData
from app.exchanges.base import ExchangeError, HttpExchangeAdapter, funding_cycle_at


class HyperliquidAdapter(HttpExchangeAdapter):
    def __init__(self, base_url: str = "https://api.hyperliquid.xyz"):
        super().__init__("hyperliquid", base_url)

    async def fetch_markets(self, symbols: list[str]) -> list[MarketSnapshotData]:
        payload = await self._request("POST", "/info", json={"type": "metaAndAssetCtxs"})
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        universe = payload[0].get("universe", [])
        contexts = payload[1]
        wanted = set(symbols)
        output = []
        for asset, context in zip(universe, contexts):
            symbol = self.normalize_symbol(asset.get("name"))
            if symbol not in wanted:
                continue
            price = self.number(context.get("markPx")) or self.number(context.get("oraclePx"))
            oi_units = self.number_or_none(context.get("openInterest"))
            if not price or oi_units is None:
                continue
            impact_prices = context.get("impactPxs") or []
            bid = self.number(impact_prices[0]) if len(impact_prices) > 0 else price
            ask = self.number(impact_prices[1]) if len(impact_prices) > 1 else price
            output.append(
                MarketSnapshotData(
                    venue=self.name,
                    symbol=symbol,
                    mark_price=price,
                    open_interest=oi_units * price,
                    bid=bid,
                    ask=ask,
                    observed_at=self.observed_at(),
                )
            )
        return output

    async def fetch_funding_history(
        self,
        symbols: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[FundingSettlementData]:
        rows: list[FundingSettlementData] = []
        start_ms = int(start_time.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end_time.astimezone(timezone.utc).timestamp() * 1000)
        for requested_symbol in symbols:
            coin = requested_symbol.removesuffix("-PERP")
            try:
                payload = await self._request(
                    "POST",
                    "/info",
                    json={
                        "type": "fundingHistory",
                        "coin": coin,
                        "startTime": start_ms,
                        "endTime": end_ms,
                    },
                )
            except ExchangeError:
                continue
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                rate = self.number_or_none(item.get("fundingRate"))
                timestamp_ms = self.number_or_none(item.get("time"))
                if rate is None or timestamp_ms is None:
                    continue
                settled_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
                rows.append(
                    FundingSettlementData(
                        venue=self.name,
                        symbol=self.normalize_symbol(item.get("coin") or requested_symbol),
                        funding_rate=rate,
                        funding_rate_native=rate,
                        funding_interval_hours=1.0,
                        funding_cycle_at=funding_cycle_at(settled_at, 1.0),
                        settled_at=settled_at,
                        source="hyperliquid_funding_history",
                    )
                )
        return rows

    async def stream_markets(self, symbols: list[str]):
        async with connect(
            "wss://api.hyperliquid.xyz/ws", ping_interval=20, ping_timeout=20
        ) as socket:
            for symbol in symbols:
                await socket.send(
                    json.dumps(
                        {
                            "method": "subscribe",
                            "subscription": {
                                "type": "activeAssetCtx",
                                "coin": symbol.removesuffix("-PERP"),
                            },
                        }
                    )
                )
            async for raw_message in socket:
                payload = json.loads(raw_message)
                if payload.get("channel") != "activeAssetCtx":
                    continue
                data = payload.get("data", {})
                context = data.get("ctx", {})
                symbol = self.normalize_symbol(data.get("coin"))
                price = self.number(context.get("markPx")) or self.number(context.get("oraclePx"))
                oi_units = self.number_or_none(context.get("openInterest"))
                if price and oi_units is not None:
                    yield MarketSnapshotData(
                        venue=self.name,
                        symbol=symbol,
                        mark_price=price,
                        open_interest=oi_units * price,
                        bid=price,
                        ask=price,
                        observed_at=self.observed_at(),
                    )
