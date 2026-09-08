from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import FundingSnapshot


@dataclass(frozen=True)
class HistoricalSpreadStats:
    long_avg_rate: float
    short_avg_rate: float
    historical_gross_hourly: float
    historical_net_apr_pct: float
    snapshot_count: int
    spread_stability_pct: float


class HistoricalFundingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        lookback_days: int = 3,
    ):
        self.session_factory = session_factory
        self.lookback_days = lookback_days

    async def get_historical_stats(
        self,
        fee_drag: float = 0.0,
    ) -> dict[tuple[str, str, str], HistoricalSpreadStats]:
        """Returns stats keyed by (symbol, venue1, venue2) over up to 3 days."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.lookback_days)

        statement = (
            select(FundingSnapshot)
            .where(FundingSnapshot.observed_at >= cutoff)
            .order_by(FundingSnapshot.observed_at.asc())
        )

        async with self.session_factory() as session:
            snapshots = list((await session.execute(statement)).scalars())

        by_venue_sym: dict[tuple[str, str], list[FundingSnapshot]] = {}
        for snap in snapshots:
            key = (snap.venue.lower(), snap.symbol.upper())
            by_venue_sym.setdefault(key, []).append(snap)

        stats_map: dict[tuple[str, str, str], HistoricalSpreadStats] = {}
        symbols = {snap.symbol.upper() for snap in snapshots}

        for symbol in symbols:
            venues = [v for (v, s) in by_venue_sym if s == symbol]
            for i, v1 in enumerate(venues):
                for v2 in venues[i + 1 :]:
                    s1_list = by_venue_sym[(v1, symbol)]
                    s2_list = by_venue_sym[(v2, symbol)]
                    avg1 = sum(s.funding_rate for s in s1_list) / len(s1_list)
                    avg2 = sum(s.funding_rate for s in s2_list) / len(s2_list)

                    long_v, short_v, l_avg, s_avg, l_list, s_list = (
                        (v1, v2, avg1, avg2, s1_list, s2_list)
                        if avg1 <= avg2
                        else (v2, v1, avg2, avg1, s2_list, s1_list)
                    )

                    gross_hourly = s_avg - l_avg
                    net_hourly = gross_hourly - fee_drag
                    net_apr = net_hourly * 24 * 365 * 100
                    total_count = len(l_list) + len(s_list)

                    short_rates = [s.funding_rate for s in s_list]
                    long_rates = [s.funding_rate for s in l_list]
                    pos_cases = sum(
                        1
                        for sr in short_rates
                        for lr in long_rates[: len(short_rates)]
                        if sr >= lr
                    )
                    comparisons = max(1, len(short_rates) * min(len(long_rates), len(short_rates)))
                    stability = min(100.0, (pos_cases / comparisons) * 100.0)

                    stats = HistoricalSpreadStats(
                        long_avg_rate=l_avg,
                        short_avg_rate=s_avg,
                        historical_gross_hourly=gross_hourly,
                        historical_net_apr_pct=net_apr,
                        snapshot_count=total_count,
                        spread_stability_pct=stability,
                    )
                    stats_map[(symbol, long_v, short_v)] = stats
        return stats_map
