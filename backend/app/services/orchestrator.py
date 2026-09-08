import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.config import Settings
from app.services.market import MarketEngine
from app.services.positions import PositionService
from app.services.simulation import SimulationService

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        market: MarketEngine,
        positions: PositionService,
        broadcast: Callable[[dict], Awaitable[None]] | None = None,
        simulation: SimulationService | None = None,
    ):
        self.settings = settings
        self.market = market
        self.positions = positions
        self.broadcast = broadcast
        self.simulation = simulation
        self._task: asyncio.Task | None = None

    async def run_once(self) -> int:
        opportunities = await self.market.refresh()
        await self.positions.evaluate_risk(opportunities)
        if self.simulation:
            await self.simulation.evaluate_and_trade(opportunities)
        await self._publish(opportunities)
        return len(opportunities)

    async def _publish(self, opportunities) -> None:
        if self.broadcast:
            opps = opportunities or list(self.market.latest.values())
            positions = await self.positions.list_positions(active_only=False)
            account_data = None
            if self.simulation:
                try:
                    account = await self.simulation.get_account()
                    account_data = {
                        "id": account.id,
                        "initial_balance": account.initial_balance,
                        "current_balance": account.current_balance,
                        "allocated_balance": account.allocated_balance,
                        "total_realized_pnl": account.total_realized_pnl,
                        "updated_at": (
                            account.updated_at.isoformat() if account.updated_at else None
                        ),
                    }
                except Exception:
                    pass
            await self.broadcast(
                {
                    "type": "market_update",
                    "data": {
                        "opportunities": [self.market._to_dict(item) for item in opps],
                        "positions": [self._position_dict(item) for item in positions],
                        "account": account_data,
                    },
                }
            )
            cache = getattr(self.market, "cache", None)
            if cache is not None:
                try:
                    from app.services.telemetry import telemetry
                    await cache.set_json("telemetry:throughput", telemetry.get_throughput())
                except Exception:
                    pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="market-refresh-loop")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
                if self.settings.enable_exchange_websockets:
                    await self._stream_loop()
                else:
                    await self._poll_loop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("market ingestion cycle failed")
            await asyncio.sleep(self.settings.market_poll_seconds)

    async def _stream_loop(self) -> None:
        stream = self.market.exchange_service.stream_all(self.settings.rest_fallback_seconds)
        try:
            async for first in stream:
                batch = [first]
                try:
                    async with asyncio.timeout(self.settings.websocket_batch_ms / 1000):
                        while True:
                            batch.append(await anext(stream))
                except (TimeoutError, StopAsyncIteration):
                    pass
                opportunities = await self.market.ingest(batch)
                await self.positions.evaluate_risk(opportunities)
                if self.simulation:
                    await self.simulation.evaluate_and_trade(opportunities)
                await self._publish(opportunities)
        finally:
            await stream.aclose()

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.market_poll_seconds)
            await self.run_once()

    @staticmethod
    def _position_dict(position) -> dict:
        return {
            "id": position.id,
            "opportunity_id": position.opportunity_id,
            "symbol": position.symbol,
            "long_venue": position.long_venue,
            "short_venue": position.short_venue,
            "size_usd": position.size_usd,
            "leg_size_usd": getattr(position, "leg_size_usd", position.size_usd),
            "long_entry_price": getattr(position, "long_entry_price", 0.0),
            "short_entry_price": getattr(position, "short_entry_price", 0.0),
            "entry_basis_bps": getattr(position, "entry_basis_bps", 0.0),
            "current_long_price": getattr(
                position, "current_long_price", position.long_entry_price
            ),
            "current_short_price": getattr(
                position, "current_short_price", position.short_entry_price
            ),
            "current_basis_bps": getattr(position, "current_basis_bps", position.entry_basis_bps),
            "funding_pnl_usd": getattr(position, "funding_pnl_usd", 0.0),
            "long_funding_pnl_usd": getattr(position, "long_funding_pnl_usd", 0.0),
            "short_funding_pnl_usd": getattr(position, "short_funding_pnl_usd", 0.0),
            "settled_funding_pnl_usd": getattr(position, "settled_funding_pnl_usd", 0.0),
            "settled_long_funding_pnl_usd": getattr(
                position, "settled_long_funding_pnl_usd", 0.0
            ),
            "settled_short_funding_pnl_usd": getattr(
                position, "settled_short_funding_pnl_usd", 0.0
            ),
            "accrued_funding_pnl_usd": getattr(position, "accrued_funding_pnl_usd", 0.0),
            "accrued_long_funding_pnl_usd": getattr(
                position, "accrued_long_funding_pnl_usd", 0.0
            ),
            "accrued_short_funding_pnl_usd": getattr(
                position, "accrued_short_funding_pnl_usd", 0.0
            ),
            "basis_pnl_usd": getattr(position, "basis_pnl_usd", 0.0),
            "entry_fee_usd": getattr(position, "entry_fee_usd", 0.0),
            "exit_fee_usd": getattr(position, "exit_fee_usd", 0.0),
            "fees_usd": getattr(position, "fees_usd", 0.0),
            "status": position.status,
            "negative_hours": position.negative_hours,
            "opened_at": position.opened_at.isoformat(),
            "updated_at": position.updated_at.isoformat(),
            "open_reason": getattr(position, "open_reason", None),
            "closed_at": position.closed_at.isoformat() if position.closed_at else None,
            "close_reason": position.close_reason,
            "entry_net_apr_pct": getattr(position, "entry_net_apr_pct", None),
            "entry_historical_apr_pct": getattr(position, "entry_historical_apr_pct", None),
            "entry_long_funding_rate": getattr(position, "entry_long_funding_rate", None),
            "entry_short_funding_rate": getattr(position, "entry_short_funding_rate", None),
            "entry_rate_observed_at": (
                position.entry_rate_observed_at.isoformat()
                if getattr(position, "entry_rate_observed_at", None)
                else None
            ),
            "last_net_apr_pct": getattr(position, "last_net_apr_pct", None),
            "last_long_funding_rate": getattr(position, "last_long_funding_rate", None),
            "last_short_funding_rate": getattr(position, "last_short_funding_rate", None),
            "last_rate_observed_at": (
                position.last_rate_observed_at.isoformat()
                if getattr(position, "last_rate_observed_at", None)
                else None
            ),
        }
