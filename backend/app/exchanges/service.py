import asyncio
import logging
from datetime import datetime

from app.config import Settings
from app.domain.types import FundingSettlementData, MarketSnapshotData
from app.exchanges.aevo import AevoAdapter
from app.exchanges.base import ExchangeAdapter
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.exchanges.lighter import LighterAdapter

logger = logging.getLogger(__name__)


class ExchangeService:
    def __init__(self, adapters: list[ExchangeAdapter], symbols: list[str]):
        self.adapters = adapters
        self.symbols = symbols

    async def fetch_all(self) -> list[MarketSnapshotData]:
        results = await asyncio.gather(
            *(adapter.fetch_markets(self.symbols) for adapter in self.adapters),
            return_exceptions=True,
        )
        snapshots: list[MarketSnapshotData] = []
        for adapter, result in zip(self.adapters, results):
            if isinstance(result, Exception):
                logger.warning("market adapter %s unavailable: %s", adapter.name, result)
                continue
            snapshots.extend(result)
        if not snapshots:
            raise RuntimeError("all exchange market adapters failed")
        return snapshots

    async def close(self) -> None:
        for adapter in self.adapters:
            close = getattr(adapter, "aclose", None)
            if close is not None:
                await close()

    async def fetch_funding_history(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FundingSettlementData]:
        """Fetch confirmed settlements; failures never become live-rate fallbacks."""
        results = await asyncio.gather(
            *(
                adapter.fetch_funding_history(self.symbols, start_time, end_time)
                for adapter in self.adapters
            ),
            return_exceptions=True,
        )
        rows: list[FundingSettlementData] = []
        for adapter, result in zip(self.adapters, results):
            if isinstance(result, Exception):
                logger.warning("funding history %s unavailable: %s", adapter.name, result)
                continue
            rows.extend(result)
        return rows

    async def stream_all(self, rest_fallback_seconds: int = 120):
        queue: asyncio.Queue[MarketSnapshotData] = asyncio.Queue(maxsize=1000)

        async def consume(adapter: ExchangeAdapter) -> None:
            stream_failures = 0
            while True:
                try:
                    async for snapshot in adapter.stream_markets(self.symbols):
                        await queue.put(snapshot)
                    raise RuntimeError("market stream ended")
                except NotImplementedError:
                    await self._poll_fallback(adapter, queue, rest_fallback_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("market stream %s disconnected: %s", adapter.name, exc)
                    stream_failures += 1
                    if stream_failures >= 3:
                        await self._fetch_once(adapter, queue)
                        stream_failures = 0
                    await asyncio.sleep(5)

        tasks = [asyncio.create_task(consume(adapter)) for adapter in self.adapters]
        try:
            while True:
                yield await queue.get()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_fallback(
        self,
        adapter: ExchangeAdapter,
        queue: asyncio.Queue[MarketSnapshotData],
        interval_seconds: int,
    ) -> None:
        while True:
            try:
                await self._fetch_once(adapter, queue)
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("REST fallback %s failed: %s", adapter.name, exc)
                await asyncio.sleep(5)

    async def _fetch_once(
        self, adapter: ExchangeAdapter, queue: asyncio.Queue[MarketSnapshotData]
    ) -> None:
        for snapshot in await adapter.fetch_markets(self.symbols):
            await queue.put(snapshot)


def build_exchange_service(settings: Settings) -> ExchangeService:
    return ExchangeService(
        [
            HyperliquidAdapter(),
            AevoAdapter(
                refresh_interval_seconds=settings.aevo_refresh_interval_seconds,
                request_interval_seconds=settings.aevo_request_interval_seconds,
                max_concurrent_requests=settings.aevo_max_concurrent_requests,
            ),
            LighterAdapter(),
        ],
        settings.symbols,
    )
