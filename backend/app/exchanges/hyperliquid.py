import json

from websockets import connect

from app.domain.types import MarketSnapshotData
from app.exchanges.base import HttpExchangeAdapter, funding_cycle_at


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
            funding = self.number_or_none(context.get("funding"))
            oi_units = self.number_or_none(context.get("openInterest"))
            if not price or funding is None or oi_units is None:
                continue
            observed_at = self.observed_at()
            impact_prices = context.get("impactPxs") or []
            bid = self.number(impact_prices[0]) if len(impact_prices) > 0 else price
            ask = self.number(impact_prices[1]) if len(impact_prices) > 1 else price
            output.append(
                MarketSnapshotData(
                    venue=self.name,
                    symbol=symbol,
                    funding_rate=funding,
                    mark_price=price,
                    open_interest=oi_units * price,
                    bid=bid,
                    ask=ask,
                    observed_at=observed_at,
                    funding_rate_native=funding,
                    funding_interval_hours=1.0,
                    funding_cycle_at=funding_cycle_at(observed_at),
                )
            )
        return output

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
                funding = self.number_or_none(context.get("funding"))
                oi_units = self.number_or_none(context.get("openInterest"))
                if price and funding is not None and oi_units is not None:
                    observed_at = self.observed_at()
                    yield MarketSnapshotData(
                        venue=self.name,
                        symbol=symbol,
                        funding_rate=funding,
                        mark_price=price,
                        open_interest=oi_units * price,
                        bid=price,
                        ask=price,
                        observed_at=observed_at,
                        funding_rate_native=funding,
                        funding_interval_hours=1.0,
                        funding_cycle_at=funding_cycle_at(observed_at),
                    )
