from dataclasses import asdict
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import Cache
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.types import MarketSnapshotData, OpportunityData
from app.exchanges.service import ExchangeService
from app.services.historical_funding import HistoricalFundingService
from app.services.telemetry import telemetry

SNAPSHOT_CACHE_KEY = "market:snapshots"
SNAPSHOT_CACHE_TTL_SECONDS = 120


class MarketEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        exchange_service: ExchangeService,
        cache: Cache,
        calculator_config: CalculatorConfig,
        historical_service: HistoricalFundingService | None = None,
    ):
        self.session_factory = session_factory
        self.exchange_service = exchange_service
        self.cache = cache
        self.calculator_config = calculator_config
        self.historical_service = historical_service or HistoricalFundingService(
            session_factory, min_cycles=calculator_config.min_history_cycles
        )
        self.latest: dict[str, OpportunityData] = {}
        self.latest_snapshots: dict[tuple[str, str], MarketSnapshotData] = {}
        self.last_refresh: datetime | None = None

    async def refresh(self) -> list[OpportunityData]:
        return await self.ingest(await self.exchange_service.fetch_all())

    async def ingest(self, snapshots: list[MarketSnapshotData]) -> list[OpportunityData]:
        if not snapshots:
            return list(self.latest.values())
        for snapshot in snapshots:
            telemetry.record_message(snapshot.venue)
            self.latest_snapshots[(snapshot.venue.lower(), snapshot.symbol.upper())] = snapshot

        await self.cache.set_json(
            SNAPSHOT_CACHE_KEY,
            [self.snapshot_to_dict(value) for value in self.latest_snapshots.values()],
            ttl_seconds=SNAPSHOT_CACHE_TTL_SECONDS,
        )
        symbols = sorted({snapshot.symbol.upper() for snapshot in snapshots})
        await self.historical_service.sync_confirmed_history(
            self.exchange_service, symbols=symbols
        )
        historical_stats = await self.historical_service.get_historical_stats()
        opportunities = calculate_opportunities(
            list(self.latest_snapshots.values()), self.calculator_config, historical_stats
        )
        self.latest = {item.id: item for item in opportunities}
        self.last_refresh = max(snapshot.observed_at for snapshot in snapshots)
        await self.cache.set_json(
            "market:opportunities", [self._to_dict(item) for item in opportunities]
        )
        return list(self.latest.values())

    async def list_opportunities(self, refresh: bool = False) -> list[OpportunityData]:
        if refresh:
            await self.refresh()
        elif not self.latest and not await self._load_cached():
            await self.refresh()
        if not self.latest_snapshots:
            await self.load_cached_snapshots()
        return list(self.latest.values())

    async def get_opportunity(self, opportunity_id: str) -> OpportunityData | None:
        if not self.latest and not await self._load_cached():
            await self.refresh()
        if not self.latest_snapshots:
            await self.load_cached_snapshots()
        return self.latest.get(opportunity_id)

    def apply_cached_opportunities(self, values: list[dict]) -> None:
        opportunities = [self._from_dict(value) for value in values]
        if opportunities:
            self.latest = {item.id: item for item in opportunities}
            self.last_refresh = max(item.observed_at for item in opportunities)

    def apply_cached_snapshots(self, values: list[dict]) -> bool:
        snapshots: dict[tuple[str, str], MarketSnapshotData] = {}
        for value in values:
            try:
                snapshot = MarketSnapshotData(
                    venue=str(value["venue"]),
                    symbol=str(value["symbol"]),
                    mark_price=float(value["mark_price"]),
                    open_interest=float(value["open_interest"]),
                    bid=float(value["bid"]),
                    ask=float(value["ask"]),
                    observed_at=datetime.fromisoformat(str(value["observed_at"])),
                )
            except (KeyError, TypeError, ValueError):
                continue
            key = (snapshot.venue.lower(), snapshot.symbol.upper())
            snapshots[key] = snapshot
        if not snapshots:
            return False
        self.latest_snapshots.update(snapshots)
        self.last_refresh = max(snapshot.observed_at for snapshot in snapshots.values())
        return True

    async def load_cached_snapshots(self) -> bool:
        values = await self.cache.get_json(SNAPSHOT_CACHE_KEY)
        if not isinstance(values, list):
            return False
        return self.apply_cached_snapshots(values)

    async def _load_cached(self) -> bool:
        values = await self.cache.get_json("market:opportunities")
        if not isinstance(values, list) or not values:
            return False
        self.apply_cached_opportunities(values)
        return True

    @staticmethod
    def _to_dict(opportunity: OpportunityData) -> dict:
        value = asdict(opportunity)
        value["observed_at"] = opportunity.observed_at.isoformat()
        if opportunity.funding_history_latest_cycle:
            value["funding_history_latest_cycle"] = (
                opportunity.funding_history_latest_cycle.isoformat()
            )
        return value

    @staticmethod
    def snapshot_to_dict(snapshot: MarketSnapshotData) -> dict:
        """Serialize execution/liquidity data without any funding-rate fields."""
        return {
            "venue": snapshot.venue,
            "symbol": snapshot.symbol,
            "mark_price": snapshot.mark_price,
            "open_interest": snapshot.open_interest,
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "observed_at": snapshot.observed_at.isoformat(),
        }

    @staticmethod
    def _from_dict(value: dict) -> OpportunityData:
        parsed = dict(value)
        parsed["observed_at"] = datetime.fromisoformat(parsed["observed_at"])
        latest_cycle = parsed.get("funding_history_latest_cycle")
        if latest_cycle:
            parsed["funding_history_latest_cycle"] = datetime.fromisoformat(latest_cycle)
        return OpportunityData(**parsed)
