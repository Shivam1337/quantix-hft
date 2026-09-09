import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.types import FundingSettlementData
from app.exchanges.base import ExchangeAdapter
from app.models import FundingSettlement


@dataclass(frozen=True)
class HistoricalSpreadStats:
    long_venue: str
    short_venue: str
    long_avg_rate: float
    short_avg_rate: float
    historical_gross_hourly: float
    historical_net_apr_pct: float
    snapshot_count: int
    spread_stability_pct: float
    oldest_cycle: datetime
    latest_cycle: datetime


class HistoricalFundingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        lookback_days: int = 3,
        min_cycles: int = 6,
        sync_interval_seconds: int = 300,
    ):
        self.session_factory = session_factory
        self.lookback_days = lookback_days
        self.min_cycles = max(2, min_cycles)
        self.sync_interval = timedelta(seconds=max(30, sync_interval_seconds))
        self._last_sync_at: datetime | None = None
        self._sync_lock = asyncio.Lock()

    async def sync_confirmed_history(
        self,
        exchange_service,
        symbols: list[str],
        now: datetime | None = None,
    ) -> int:
        """Persist only rows returned by official exchange history endpoints."""
        current = self._aware(now or datetime.now(timezone.utc))
        if self._last_sync_at and current - self._last_sync_at < self.sync_interval:
            return 0
        async with self._sync_lock:
            if self._last_sync_at and current - self._last_sync_at < self.sync_interval:
                return 0
            fetcher = getattr(exchange_service, "fetch_funding_history", None)
            self._last_sync_at = current
            if fetcher is None or getattr(
                fetcher, "__func__", None
            ) is ExchangeAdapter.fetch_funding_history:
                return 0
            start = current - timedelta(days=self.lookback_days)
            rows = await fetcher(start, current)
            normalized = []
            for row in rows:
                if not row.funding_cycle_at:
                    continue
                value = self._normalize(row)
                if start <= value.funding_cycle_at <= current:
                    normalized.append(value)
            if not normalized:
                return 0

            async with self.session_factory() as session:
                existing_rows = list(
                    (
                        await session.execute(
                            select(FundingSettlement).where(
                                FundingSettlement.funding_cycle_at >= start
                            )
                        )
                    ).scalars()
                )
                existing = {
                    (row.venue.lower(), row.symbol.upper(), self._aware(row.funding_cycle_at))
                    for row in existing_rows
                }
                inserted = 0
                for row in normalized:
                    key = (row.venue.lower(), row.symbol.upper(), row.funding_cycle_at)
                    if key in existing:
                        continue
                    session.add(
                        FundingSettlement(
                            venue=row.venue.lower(),
                            symbol=row.symbol.upper(),
                            funding_rate=row.funding_rate,
                            funding_rate_native=row.funding_rate_native,
                            funding_interval_hours=row.funding_interval_hours,
                            funding_cycle_at=row.funding_cycle_at,
                            settled_at=row.settled_at,
                            source=row.source,
                        )
                    )
                    existing.add(key)
                    inserted += 1
                await session.commit()
                return inserted

    async def get_historical_stats(
        self,
        fee_drag: float = 0.0,
        now: datetime | None = None,
    ) -> dict[tuple[str, str, str], HistoricalSpreadStats]:
        """Build stats from aligned, contiguous, confirmed cycles only."""
        current = self._aware(now or datetime.now(timezone.utc))
        cutoff = current - timedelta(days=self.lookback_days)
        statement = (
            select(FundingSettlement)
            .where(FundingSettlement.funding_cycle_at >= cutoff)
            .order_by(FundingSettlement.funding_cycle_at.asc())
        )
        async with self.session_factory() as session:
            settlements = list((await session.execute(statement)).scalars())

        by_market: dict[tuple[str, str], dict[datetime, FundingSettlement]] = {}
        for settlement in settlements:
            key = (settlement.venue.lower(), settlement.symbol.upper())
            by_market.setdefault(key, {})[self._aware(settlement.funding_cycle_at)] = settlement

        stats_map: dict[tuple[str, str, str], HistoricalSpreadStats] = {}
        symbols = sorted({symbol for _, symbol in by_market})
        venues_by_symbol = {
            symbol: sorted(venue for venue, candidate in by_market if candidate == symbol)
            for symbol in symbols
        }
        for symbol in symbols:
            venues = venues_by_symbol[symbol]
            for index, first_venue in enumerate(venues):
                for second_venue in venues[index + 1 :]:
                    first = by_market[(first_venue, symbol)]
                    second = by_market[(second_venue, symbol)]
                    cycles = self._aligned_contiguous_cycles(first, second)
                    if len(cycles) < self.min_cycles:
                        continue
                    first_rates = [first[cycle].funding_rate for cycle in cycles]
                    second_rates = [second[cycle].funding_rate for cycle in cycles]
                    first_median = _median(first_rates)
                    second_median = _median(second_rates)
                    if first_median <= second_median:
                        long_venue, short_venue = first_venue, second_venue
                        long_rates, short_rates = first_rates, second_rates
                    else:
                        long_venue, short_venue = second_venue, first_venue
                        long_rates, short_rates = second_rates, first_rates
                    spreads = [short - long for long, short in zip(long_rates, short_rates)]
                    gross_hourly = _median(spreads)
                    stability = sum(spread >= 0 for spread in spreads) / len(spreads) * 100
                    stats_map[(symbol, long_venue, short_venue)] = HistoricalSpreadStats(
                        long_venue=long_venue,
                        short_venue=short_venue,
                        long_avg_rate=_median(long_rates),
                        short_avg_rate=_median(short_rates),
                        historical_gross_hourly=gross_hourly,
                        historical_net_apr_pct=(gross_hourly - fee_drag) * 24 * 365 * 100,
                        snapshot_count=len(cycles),
                        spread_stability_pct=stability,
                        oldest_cycle=cycles[0],
                        latest_cycle=cycles[-1],
                    )
        return stats_map

    async def list_settlements(
        self,
        venue: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[FundingSettlement]:
        statement = select(FundingSettlement).order_by(
            FundingSettlement.funding_cycle_at.desc()
        ).limit(limit)
        if venue:
            statement = statement.where(FundingSettlement.venue == venue.lower())
        if symbol:
            statement = statement.where(FundingSettlement.symbol == symbol.upper())
        async with self.session_factory() as session:
            return list((await session.execute(statement)).scalars())

    @classmethod
    def _aligned_contiguous_cycles(
        cls,
        first: dict[datetime, FundingSettlement],
        second: dict[datetime, FundingSettlement],
    ) -> list[datetime]:
        common = sorted(set(first) & set(second))
        if not common:
            return []
        expected_seconds = max(
            3600,
            int(
                max(
                    first[cycle].funding_interval_hours
                    for cycle in common
                )
                * 3600
            ),
        )
        for previous, cycle in zip(common, common[1:]):
            gap = (cycle - previous).total_seconds()
            if abs(gap - expected_seconds) > 60:
                return []
        return common

    @staticmethod
    def _normalize(row: FundingSettlementData) -> FundingSettlementData:
        cycle = HistoricalFundingService._aware(row.funding_cycle_at).replace(microsecond=0)
        return FundingSettlementData(
            venue=row.venue.lower(),
            symbol=row.symbol.upper(),
            funding_rate=row.funding_rate,
            funding_rate_native=row.funding_rate_native,
            funding_interval_hours=row.funding_interval_hours,
            funding_cycle_at=cycle,
            settled_at=HistoricalFundingService._aware(row.settled_at),
            source=row.source,
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
