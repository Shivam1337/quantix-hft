import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from websockets import connect

from app.domain.types import FundingSettlementData, MarketSnapshotData
from app.exchanges.base import HttpExchangeAdapter, funding_cycle_at


class LighterAdapter(HttpExchangeAdapter):
    def __init__(self, base_url: str = "https://mainnet.zklighter.elliot.ai"):
        super().__init__("lighter", base_url)
        self._market_ids: dict[str, int] = {}

    async def fetch_markets(self, symbols: list[str]) -> list[MarketSnapshotData]:
        payload = await self._request("GET", "/api/v1/orderBookDetails")
        rows = self._market_rows(payload)
        wanted = set(symbols)
        output: list[MarketSnapshotData] = []
        for row in rows:
            symbol = self.normalize_symbol(
                row.get("symbol") or row.get("market_symbol") or row.get("name")
            )
            market_id = self._market_id(row.get("market_id"))
            price = self.number_or_none(row.get("mark_price") or row.get("markPrice"))
            if symbol not in wanted or market_id is None or not price or price <= 0:
                continue
            self._market_ids[symbol] = market_id
            snapshot = self._snapshot(row, symbol, price)
            if snapshot:
                output.append(snapshot)
        return output

    async def fetch_funding_history(
        self,
        symbols: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[FundingSettlementData]:
        if not self._market_ids:
            await self.fetch_markets(symbols)
        results = await asyncio.gather(
            *(
                self._fetch_history(symbol, market_id, start_time, end_time)
                for symbol, market_id in self._market_ids.items()
                if symbol in set(symbols)
            ),
            return_exceptions=True,
        )
        rows: list[FundingSettlementData] = []
        for result in results:
            if isinstance(result, list):
                rows.extend(result)
        return rows

    async def _fetch_history(
        self,
        symbol: str,
        market_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FundingSettlementData]:
        payload = await self._request(
            "GET",
            "/api/v1/fundings",
            params={
                "market_id": market_id,
                "resolution": "1h",
                "start_timestamp": int(start_time.timestamp()),
                "end_timestamp": int(end_time.timestamp()),
                "count_back": 100,
            },
        )
        values = payload.get("fundings", []) if isinstance(payload, dict) else []
        rows: list[FundingSettlementData] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            timestamp = self.number_or_none(item.get("timestamp"))
            raw_rate = self.number_or_none(item.get("rate"))
            if timestamp is None or raw_rate is None:
                continue
            settled_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            signed_native = self._signed_native_rate(raw_rate, item.get("direction"))
            rows.append(
                FundingSettlementData(
                    venue=self.name,
                    symbol=symbol,
                    funding_rate=signed_native / 100,
                    funding_rate_native=signed_native,
                    funding_interval_hours=1.0,
                    funding_cycle_at=funding_cycle_at(settled_at, 1.0),
                    settled_at=settled_at,
                    source="lighter_funding_history",
                )
            )
        return rows

    def _snapshot(
        self, row: dict[str, Any], symbol: str, price: float
    ) -> MarketSnapshotData | None:
        oi = self.number_or_none(row.get("open_interest") or row.get("openInterest"))
        if oi is None:
            return None
        last_trade = self.number_or_none(row.get("last_trade_price")) or price
        return MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            mark_price=price,
            open_interest=oi * price if oi < price else oi,
            bid=last_trade,
            ask=last_trade,
            observed_at=self.observed_at(),
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
        oi = self.number_or_none(stats.get("open_interest"))
        bid = self.number_or_none(stats.get("best_bid_price"))
        ask = self.number_or_none(stats.get("best_ask_price"))
        if symbol not in wanted or price is None or oi is None or bid is None or ask is None:
            return None
        return MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            mark_price=price,
            open_interest=oi,
            bid=bid,
            ask=ask,
            observed_at=self._milliseconds(timestamp) or self.observed_at(),
        )

    @staticmethod
    def _signed_native_rate(rate: float, direction: Any) -> float:
        normalized = str(direction or "").strip().lower()
        if normalized == "short":
            return -abs(rate)
        if normalized == "long":
            return abs(rate)
        return rate

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
