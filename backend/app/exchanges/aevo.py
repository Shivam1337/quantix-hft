import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from websockets import connect

from app.domain.types import MarketSnapshotData
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
        self._open_interest: dict[str, float] = {}
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
                    await self._wait_for_request_slot()
                    funding = await self._request(
                        "GET", "/funding", params={"instrument_name": instrument_name}
                    )
                value = self._snapshot(instrument, funding)
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

    async def _wait_for_request_slot(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._rate_lock:
            now = loop.time()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + (
                self._request_interval_seconds
            )
        if delay > 0:
            await asyncio.sleep(delay)

    def _schedule_retry(self, symbol: str, retry_after_seconds: float) -> None:
        failures = self._failure_count.get(symbol, 0) + 1
        self._failure_count[symbol] = failures
        backoff = min(300.0, 30.0 * (2 ** min(failures - 1, 3)))
        delay = max(backoff, retry_after_seconds)
        self._refresh_after[symbol] = self.observed_at() + timedelta(seconds=delay)

    def _snapshot(
        self, instrument: dict[str, Any], funding: dict[str, Any]
    ) -> MarketSnapshotData | None:
        if not isinstance(instrument, dict) or not isinstance(funding, dict):
            return None
        symbol = self.normalize_symbol(instrument.get("instrument_name"))
        mark = self.number_or_none(instrument.get("mark_price"))
        funding_rate = self.number_or_none(funding.get("funding_rate"))
        funding_rate = funding_rate if funding_rate is not None else self.number_or_none(
            instrument.get("funding_rate")
        )
        markets = instrument.get("markets") or {}
        oi_contracts = self.number_or_none(markets.get("total_oi"))
        best_bid = self.number_or_none((instrument.get("best_bid") or {}).get("price"))
        best_ask = self.number_or_none((instrument.get("best_ask") or {}).get("price"))
        if (
            not mark
            or funding_rate is None
            or oi_contracts is None
            or best_bid is None
            or best_ask is None
        ):
            return None
        observed_at = self.observed_at()
        open_interest = oi_contracts * mark
        self._open_interest[symbol] = open_interest
        next_funding_at = self._epoch_datetime(funding.get("next_epoch"))
        value = MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            funding_rate=funding_rate,
            mark_price=mark,
            open_interest=open_interest,
            bid=best_bid,
            ask=best_ask,
            observed_at=observed_at,
            funding_rate_native=funding_rate,
            funding_interval_hours=1.0,
            funding_cycle_at=funding_cycle_at(next_funding_at or observed_at, 1.0),
        )
        self._latest[symbol] = value
        self._refresh_after[symbol] = observed_at + self._refresh_interval
        return value

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
            try:
                refreshed = await self._fetch_instrument(symbol)
            except Exception:
                refreshed = None
            if refreshed is not None:
                cached = refreshed
        if cached is None:
            return None
        observed_at = observed_at or self.observed_at()
        return MarketSnapshotData(
            venue=self.name,
            symbol=symbol,
            funding_rate=cached.funding_rate,
            mark_price=cached.mark_price,
            open_interest=cached.open_interest,
            bid=bid,
            ask=ask,
            observed_at=observed_at,
            funding_rate_native=cached.funding_rate_native,
            funding_interval_hours=cached.funding_interval_hours,
            funding_cycle_at=cached.funding_cycle_at,
        )

    @staticmethod
    def _epoch_datetime(value: Any) -> datetime | None:
        try:
            return datetime.fromtimestamp(float(value) / 1_000_000_000, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
