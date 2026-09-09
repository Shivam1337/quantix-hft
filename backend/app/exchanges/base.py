from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.domain.types import MarketSnapshotData


class ExchangeError(RuntimeError):
    pass


class ExchangeRateLimited(ExchangeError):
    def __init__(self, message: str, retry_after_seconds: float = 60.0):
        super().__init__(message)
        self.retry_after_seconds = max(1.0, retry_after_seconds)


class ExchangeAdapter(ABC):
    name: str

    @abstractmethod
    async def fetch_markets(self, symbols: list[str]) -> list[MarketSnapshotData]:
        raise NotImplementedError

    async def stream_markets(self, symbols: list[str]) -> AsyncIterator[MarketSnapshotData]:
        if False:
            yield MarketSnapshotData  # pragma: no cover
        raise NotImplementedError


class HttpExchangeAdapter(ExchangeAdapter):
    def __init__(self, name: str, base_url: str, timeout_seconds: float = 8):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _clean_endpoint(path: str) -> str:
        clean = "/" + path.lstrip("/")
        if clean.startswith("/instrument/"):
            return "/instrument/{symbol}"
        if clean.startswith("/api/v1/fundings"):
            return "/api/v1/fundings"
        return clean

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        clean_path = self._clean_endpoint(path)
        response: httpx.Response | None = None
        try:
            client = self._client_for_request()
            response = await client.request(
                method, f"{self.base_url}/{path.lstrip('/')}", **kwargs
            )
            from app.services.telemetry import telemetry

            telemetry.record_rest_call(
                venue=self.name,
                method=method,
                endpoint=clean_path,
                status_code=response.status_code,
            )
            if response.status_code == 429:
                raise ExchangeRateLimited(
                    f"{self.name} request rate limited for {clean_path}",
                    retry_after_seconds=self._retry_after_seconds(
                        response.headers.get("Retry-After")
                    ),
                )
            response.raise_for_status()
            return response.json()
        except ExchangeRateLimited:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            if response is None:
                from app.services.telemetry import telemetry

                telemetry.record_rest_call(
                    venue=self.name,
                    method=method,
                    endpoint=clean_path,
                    status_code=500,
                )
            raise ExchangeError(f"{self.name} market request failed: {exc}") from exc

    def _client_for_request(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float:
        if not value:
            return 60.0
        try:
            return max(1.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(1.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return 60.0

    @staticmethod
    def normalize_symbol(value: Any) -> str:
        symbol = str(value or "").upper().replace("/", "-")
        return symbol if symbol.endswith("-PERP") else f"{symbol}-PERP"

    @staticmethod
    def number(value: Any, default: float = 0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def number_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def observed_at() -> datetime:
        return datetime.now(timezone.utc)


def funding_cycle_at(observed_at: datetime, interval_hours: float = 1.0) -> datetime:
    """Return a stable UTC bucket for exchanges without a cycle timestamp."""
    seconds = max(60, int(interval_hours * 3600))
    timestamp = observed_at.astimezone(timezone.utc).timestamp()
    bucket = int(timestamp // seconds) * seconds
    return datetime.fromtimestamp(bucket, tz=timezone.utc).replace(microsecond=0)
