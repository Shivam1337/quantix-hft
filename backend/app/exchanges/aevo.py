import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from websockets import connect

from app.domain.types import FundingSettlementData, MarketSnapshotData
from app.exchanges.base import (
    ExchangeError,
    ExchangeRateLimited,
    HttpExchangeAdapter,
    funding_cycle_at,
)


class AevoAdapter(HttpExchangeAdapter):
    def __init__(
        self,
        base_url: str = "https://api.aevo.xyz",
        refresh_interval_seconds: int = 300,
        request_interval_seconds: float = 1.0,
        max_concurrent_requests: int = 2,
    ):
        super().__init__("aevo", base_url)
        self._latest: dict[str, MarketSnapshotData] = {}
        self._refresh_after: dict[str, datetime] = {}
        self._refresh_interval = timedelta(seconds=max(60, refresh_interval_seconds))
        self._request_interval_seconds = max(0.0, request_interval_seconds)
        self._request_slots = asyncio.Semaphore(max(1, max_concurrent_requests))
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._rate_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._failure_count: dict[str, int] = {}

    async def fetch_markets(self, symbols: list[str]) -> list[MarketSnapshotData]:
        results = await asyncio.gather(
            *(self._fetch_instrument(symbol) for symbol in symbols), return_exceptions=True
        )
        return [value for value in results if isinstance(value, MarketSnapshotData)]

    async def _fetch_instrument(self, symbol: str) -> MarketSnapshotData | None:
        instrument_name = symbol if symbol.endswith("-PERP") else f"{symbol}-PERP"
        normalized = self.normalize_symbol(instrument_name)
        lock = self._refresh_locks.setdefault(normalized, asyncio.Lock())
        async with lock:
            now = self.observed_at()
            if now < self._refresh_after.get(
                normalized, datetime.min.replace(tzinfo=timezone.utc)
            ):
                return self._latest.get(normalized)
            try:
                async with self._request_slots:
                    await self._wait_for_request_slot()
                    instrument = await self._request("GET", f"/instrument/{instrument_name}")
                value = self._snapshot(instrument)
            except ExchangeRateLimited as exc:
                self._schedule_retry(normalized, exc.retry_after_seconds)
                return self._latest.get(normalized)
            except ExchangeError:
                self._schedule_retry(normalized, 30.0)
                return self._latest.get(normalized)
            if value is None:
                self._schedule_retry(normalized, 30.0)
            else:
                self._failure_count.pop(normalized, None)
            return value

    async def fetch_funding_history(
        self,
        symbols: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[FundingSettlementData]:
        results = await asyncio.gather(
            *(self._fetch_history(symbol, start_time, end_time) for symbol in symbols),
            return_exceptions=True,
        )
        rows: list[FundingSettlementData] = []
        for result in results:
            if isinstance(result, list):
                rows.extend(result)
        return rows

    async def _fetch_history(
        self, symbol: str, start_time: datetime, end_time: datetime
    ) -> list[FundingSettlementData]:
        instrument_name = symbol if symbol.endswith("-PERP") else f"{symbol}-PERP"
        try:
            async with self._request_slots:
                await self._wait_for_request_slot()
                payload = await self._request(
                    "GET",
                    "/funding-history",
                    params={
                        "instrument_name": instrument_name,
                        "start_timestamp": int(start_time.timestamp() * 1_000_000_000),
                        "end_timestamp": int(end_time.timestamp() * 1_000_000_000),
                        "limit": 1000,
                    },
                )
        except (ExchangeError, ExchangeRateLimited):
            return []
        return self._history_rows(payload, instrument_name)

    def _snapshot(self, instrument: dict[str, Any]) -> MarketSnapshotData | None:
        if not isinstance(instrument, dict):
            return None
        symbol = self.normalize_symbol(instrument.get("instrument_name"))
        mark = self.number_or_none(instrument.get("mark_price"))
        markets = instrument.get("markets") or {}
        oi_contracts = self.number_or_none(markets.get("total_oi"))
        best_bid = self.number_or_none((instrument.get("best_bid") or {}).get("price"))
        best_ask = self.number_or_none((instrument.get("best_ask") or {}).get("price"))
        if not mark or oi_contracts is None or best_bid is None or best_ask is None:
            return None
        value = MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            mark_price=mark,
            open_interest=oi_contracts * mark,
            bid=best_bid,
            ask=best_ask,
            observed_at=self.observed_at(),
        )
        self._latest[symbol] = value
        self._refresh_after[symbol] = value.observed_at + self._refresh_interval
        return value

    @staticmethod
    def _history_rows(payload: Any, requested_symbol: str) -> list[FundingSettlementData]:
        values = payload.get("funding_history", []) if isinstance(payload, dict) else []
        rows: list[FundingSettlementData] = []
        for item in values:
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                symbol, timestamp, rate = item[:3]
            elif isinstance(item, dict):
                symbol = item.get("instrument_name") or requested_symbol
                timestamp = item.get("timestamp")
                if timestamp is None:
                    timestamp = item.get("funding_time")
                rate = item.get("funding_rate")
                if rate is None:
                    rate = item.get("rate")
            else:
                continue
            timestamp_value = AevoAdapter._epoch_datetime(timestamp)
            rate_value = AevoAdapter.number_or_none(rate)
            if timestamp_value is None or rate_value is None:
                continue
            rows.append(
                FundingSettlementData(
                    venue="aevo",
                    symbol=AevoAdapter.normalize_symbol(symbol),
                    funding_rate=rate_value,
                    funding_rate_native=rate_value,
                    funding_interval_hours=1.0,
                    funding_cycle_at=funding_cycle_at(timestamp_value, 1.0),
                    settled_at=timestamp_value,
                    source="aevo_funding_history",
                )
            )
        return rows

    async def _wait_for_request_slot(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._rate_lock:
            now = loop.time()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self._request_interval_seconds
        if delay > 0:
            await asyncio.sleep(delay)

    def _schedule_retry(self, symbol: str, retry_after_seconds: float) -> None:
        failures = self._failure_count.get(symbol, 0) + 1
        self._failure_count[symbol] = failures
        backoff = min(300.0, 30.0 * (2 ** min(failures - 1, 3)))
        self._refresh_after[symbol] = self.observed_at() + timedelta(
            seconds=max(backoff, retry_after_seconds)
        )

    async def stream_markets(self, symbols: list[str]):
        wanted = {self.normalize_symbol(symbol) for symbol in symbols}
        if not self._latest:
            await self.fetch_markets(symbols)
        channels = [
            f"book-ticker:{symbol.removesuffix('-PERP')}:PERPETUAL"
            for symbol in sorted(wanted)
        ]
        async with connect("wss://ws.aevo.xyz", ping_interval=20, ping_timeout=20) as socket:
            await socket.send(json.dumps({"op": "subscribe", "data": channels}))
            async for raw_message in socket:
                payload = json.loads(raw_message)
                data = payload.get("data", {})
                rows = data.get("tickers", []) if isinstance(data, dict) else []
                observed_at = (
                    self._epoch_datetime(data.get("timestamp"))
                    if isinstance(data, dict)
                    else None
                )
                for row in rows:
                    snapshot = await self._stream_snapshot(row, wanted, observed_at)
                    if snapshot:
                        yield snapshot

    async def _stream_snapshot(
        self,
        row: dict[str, Any],
        wanted: set[str],
        observed_at: datetime | None,
    ) -> MarketSnapshotData | None:
        symbol = self.normalize_symbol(row.get("instrument_name"))
        bid = self.number_or_none((row.get("bid") or {}).get("price"))
        ask = self.number_or_none((row.get("ask") or {}).get("price"))
        if symbol not in wanted or bid is None or ask is None:
            return None
        cached = self._latest.get(symbol)
        now = self.observed_at()
        if cached is None or now >= self._refresh_after.get(
            symbol, datetime.min.replace(tzinfo=timezone.utc)
        ):
            cached = await self._fetch_instrument(symbol)
        if cached is None:
            return None
        return MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            mark_price=cached.mark_price,
            open_interest=cached.open_interest,
            bid=bid,
            ask=ask,
            observed_at=observed_at or self.observed_at(),
        )

    @staticmethod
    def _epoch_datetime(value: Any) -> datetime | None:
        try:
            numeric = float(value)
            divisor = 1_000_000_000 if numeric > 10_000_000_000 else 1_000
            return datetime.fromtimestamp(numeric / divisor, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
