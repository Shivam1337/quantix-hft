from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import Cache
from app.domain.calculator import CalculatorConfig, calculate_opportunities
from app.domain.types import MarketSnapshotData, OpportunityData
from app.exchanges.base import funding_cycle_at as bucket_funding_cycle
from app.exchanges.service import ExchangeService
from app.models import FundingSnapshot
from app.services.historical_funding import HistoricalFundingService
from app.services.telemetry import telemetry


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
        self.historical_service = historical_service or HistoricalFundingService(session_factory)
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

        for s in snapshots:
            key = (s.venue, s.symbol)
            cycle_at = self._cycle_at(s)
            prev = self.latest_snapshots.get(key)
            if prev is not None and self._cycle_at(prev) == cycle_at:
                stable_rate = (
                    prev.funding_rate
                    if prev.funding_rate != 0 or s.funding_rate == 0
                    else s.funding_rate
                )
                stable_native = (
                    prev.funding_rate_native
                    if prev.funding_rate_native is not None
                    else s.funding_rate_native
                )
                self.latest_snapshots[key] = MarketSnapshotData(
                    venue=s.venue,
                    symbol=s.symbol,
                    funding_rate=stable_rate,
                    mark_price=s.mark_price,
                    open_interest=s.open_interest,
                    bid=s.bid,
                    ask=s.ask,
                    observed_at=s.observed_at,
                    funding_rate_native=stable_native,
                    funding_interval_hours=s.funding_interval_hours,
                    funding_cycle_at=cycle_at,
                )
            else:
                self.latest_snapshots[key] = s

        async with self.session_factory() as session:
            for s in snapshots:
                cycle_at = self._cycle_at(s)
                snap_to_store = self.latest_snapshots.get((s.venue, s.symbol), s)
                existing = await session.scalar(
                    select(FundingSnapshot).where(
                        FundingSnapshot.venue == s.venue,
                        FundingSnapshot.symbol == s.symbol,
                        FundingSnapshot.funding_cycle_at == cycle_at,
                    )
                )
                if existing is None:
                    session.add(self._to_model(snap_to_store, cycle_at))
                else:
                    self._update_model(existing, snap_to_store, cycle_at)
            await session.commit()

        fee_drag = (
            self.calculator_config.fee_schedule.pair_fee_bps("hyperliquid", "lighter") * 2 / 10_000
        ) / self.calculator_config.fee_amortization_hours
        historical_stats = await self.historical_service.get_historical_stats(fee_drag)

        opportunities = calculate_opportunities(
            list(self.latest_snapshots.values()), self.calculator_config, historical_stats
        )
        if opportunities or not self.latest:
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
        return list(self.latest.values())

    async def get_opportunity(self, opportunity_id: str) -> OpportunityData | None:
        if not self.latest and not await self._load_cached():
            await self.refresh()
        return self.latest.get(opportunity_id)

    def apply_cached_opportunities(self, values: list[dict]) -> None:
        opportunities = [self._from_dict(value) for value in values]
        if opportunities:
            self.latest = {item.id: item for item in opportunities}
            self.last_refresh = max(item.observed_at for item in opportunities)

    async def _load_cached(self) -> bool:
        values = await self.cache.get_json("market:opportunities")
        if not isinstance(values, list) or not values:
            return False
        self.apply_cached_opportunities(values)
        return True

    @staticmethod
    def _to_model(snapshot: MarketSnapshotData, cycle_at: datetime) -> FundingSnapshot:
        values = asdict(snapshot)
        values["funding_rate_native"] = (
            snapshot.funding_rate_native
            if snapshot.funding_rate_native is not None
            else snapshot.funding_rate
        )
        values["funding_cycle_at"] = cycle_at
        return FundingSnapshot(**values)

    @staticmethod
    def _update_model(
        model: FundingSnapshot, snapshot: MarketSnapshotData, cycle_at: datetime
    ) -> None:
        values = asdict(snapshot)
        values["funding_rate_native"] = (
            snapshot.funding_rate_native
            if snapshot.funding_rate_native is not None
            else snapshot.funding_rate
        )
        values["funding_cycle_at"] = cycle_at
        for key, value in values.items():
            setattr(model, key, value)

    @staticmethod
    def _cycle_at(snapshot: MarketSnapshotData) -> datetime:
        ref = snapshot.funding_cycle_at or snapshot.observed_at
        return bucket_funding_cycle(ref, snapshot.funding_interval_hours)

    @staticmethod
    def _to_dict(opportunity: OpportunityData) -> dict:
        value = asdict(opportunity)
        value["observed_at"] = opportunity.observed_at.isoformat()
        return value

    @staticmethod
    def _from_dict(value: dict) -> OpportunityData:
        parsed = dict(value)
        parsed["observed_at"] = datetime.fromisoformat(parsed["observed_at"])
        return OpportunityData(**parsed)
