import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from websockets import connect

from app.domain.types import MarketSnapshotData
from app.exchanges.base import HttpExchangeAdapter, funding_cycle_at


class LighterAdapter(HttpExchangeAdapter):
    def __init__(self, base_url: str = "https://mainnet.zklighter.elliot.ai"):
        super().__init__("lighter", base_url)
        self._market_ids: dict[str, int] = {}

    async def fetch_markets(self, symbols: list[str]) -> list[MarketSnapshotData]:
        payload = await self._request("GET", "/api/v1/orderBookDetails")
        rows = self._market_rows(payload)
        wanted = set(symbols)
        candidates = []
        for row in rows:
            symbol = self.normalize_symbol(
                row.get("symbol") or row.get("market_symbol") or row.get("name")
            )
            market_id = self._market_id(row.get("market_id"))
            price = self.number_or_none(row.get("mark_price") or row.get("markPrice"))
            if symbol in wanted and market_id is not None and price and price > 0:
                candidates.append((row, symbol, market_id, price))
                self._market_ids[symbol] = market_id
        rates = await asyncio.gather(
            *(self._fetch_latest_funding(market_id) for _, _, market_id, _ in candidates),
            return_exceptions=True,
        )
        output = []
        for (row, symbol, _, price), result in zip(candidates, rates):
            if isinstance(result, Exception) or result is None:
                continue
            native_rate, cycle_at = result
            snapshot = self._snapshot(row, symbol, price, native_rate, cycle_at)
            if snapshot:
                output.append(snapshot)
        return output

    async def _fetch_latest_funding(
        self, market_id: int
    ) -> tuple[float, datetime] | None:
        now = int(time.time())
        payload = await self._request(
            "GET",
            "/api/v1/fundings",
            params={
                "market_id": market_id,
                "resolution": "1h",
                "start_timestamp": now - 3 * 3600,
                "end_timestamp": now,
                "count_back": 3,
            },
        )
        rows = payload.get("fundings", []) if isinstance(payload, dict) else []
        rows = [row for row in rows if isinstance(row, dict)]
        if not rows:
            return None
        latest = max(rows, key=lambda row: self.number(row.get("timestamp")))
        native_rate = self.number_or_none(latest.get("rate"))
        timestamp = self.number_or_none(latest.get("timestamp"))
        if native_rate is None or timestamp is None:
            return None
        cycle_time = funding_cycle_at(datetime.fromtimestamp(timestamp, tz=timezone.utc), 1.0)
        return native_rate, cycle_time

    def _snapshot(
        self,
        row: dict[str, Any],
        symbol: str,
        price: float,
        native_rate: float,
        cycle_at: datetime,
    ) -> MarketSnapshotData | None:
        oi = self.number_or_none(row.get("open_interest") or row.get("openInterest"))
        if oi is None:
            return None
        last_trade = self.number_or_none(row.get("last_trade_price")) or price
        observed_at = self.observed_at()
        return MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            funding_rate=native_rate / 100,
            mark_price=price,
            open_interest=oi * price if oi < price else oi,
            bid=last_trade,
            ask=last_trade,
            observed_at=observed_at,
            funding_rate_native=native_rate,
            funding_interval_hours=1.0,
            funding_cycle_at=cycle_at,
        )

    async def stream_markets(self, symbols: list[str]):
        if not self._market_ids:
            await self.fetch_markets(symbols)
        wanted = {self.normalize_symbol(symbol) for symbol in symbols}
        async with connect(
            "wss://mainnet.zklighter.elliot.ai/stream", ping_interval=20, ping_timeout=20
        ) as socket:
            if await socket.recv():
                for symbol in sorted(wanted):
                    market_id = self._market_ids.get(symbol)
                    if market_id is not None:
                        await socket.send(
                            json.dumps(
                                {"type": "subscribe", "channel": f"market_stats/{market_id}"}
                            )
                        )
            async for raw_message in socket:
                payload = json.loads(raw_message)
                stats = payload.get("market_stats")
                if isinstance(stats, dict):
                    snapshot = self._stream_snapshot(stats, wanted, payload.get("timestamp"))
                    if snapshot:
                        yield snapshot

    def _stream_snapshot(
        self, stats: dict[str, Any], wanted: set[str], timestamp: Any
    ) -> MarketSnapshotData | None:
        symbol = self.normalize_symbol(stats.get("symbol"))
        price = self.number_or_none(stats.get("mark_price"))
        native_rate = self.number_or_none(stats.get("funding_rate"))
        oi = self.number_or_none(stats.get("open_interest"))
        bid = self.number_or_none(stats.get("best_bid_price"))
        ask = self.number_or_none(stats.get("best_ask_price"))
        if (
            symbol not in wanted
            or price is None
            or native_rate is None
            or oi is None
            or bid is None
            or ask is None
        ):
            return None
        observed_at = self._milliseconds(timestamp) or self.observed_at()
        cycle_raw = self._milliseconds(stats.get("funding_timestamp")) or observed_at
        cycle_at = funding_cycle_at(cycle_raw, 1.0)
        return MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            funding_rate=native_rate / 100,
            mark_price=price,
            open_interest=oi,
            bid=bid,
            ask=ask,
            observed_at=observed_at,
            funding_rate_native=native_rate,
            funding_interval_hours=1.0,
            funding_cycle_at=cycle_at,
        )

    @staticmethod
    def _market_rows(payload: Any) -> list[dict[str, Any]]:
        rows = payload.get("order_book_details", []) if isinstance(payload, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _market_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _milliseconds(value: Any) -> datetime | None:
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
