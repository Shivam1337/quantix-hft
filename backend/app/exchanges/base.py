from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import httpx

from app.domain.types import MarketSnapshotData


class ExchangeError(RuntimeError):
    pass


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
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
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
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            from app.services.telemetry import telemetry
            status = getattr(getattr(exc, "response", None), "status_code", 500)
            telemetry.record_rest_call(
                venue=self.name,
                method=method,
                endpoint=clean_path,
                status_code=status,
            )
            raise ExchangeError(f"{self.name} market request failed: {exc}") from exc

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
