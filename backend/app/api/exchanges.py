from fastapi import APIRouter, Request
from sqlalchemy import select

from app.models import FundingSnapshot
from app.schemas import ExchangeMarketRead, ExchangeSummaryRead

router = APIRouter(prefix="/api/v1/exchanges", tags=["exchanges"])

EXCHANGE_NAMES = {
    "hyperliquid": "Hyperliquid",
    "aevo": "Aevo",
    "lighter": "Lighter.xyz",
}


@router.get("", response_model=list[ExchangeSummaryRead])
async def list_exchanges(request: Request) -> list[ExchangeSummaryRead]:
    market = request.app.state.market
    service = market.exchange_service
    symbols = getattr(service, "symbols", request.app.state.settings.symbols)

    if hasattr(service, "adapters"):
        venues = [adapter.name.lower() for adapter in service.adapters]
    elif hasattr(service, "snapshots"):
        venues = list(dict.fromkeys(s.venue.lower() for s in service.snapshots))
    else:
        venues = ["hyperliquid", "aevo", "lighter"]

    if not market.latest_snapshots:
        try:
            await market.list_opportunities()
        except Exception:
            pass

    results: list[ExchangeSummaryRead] = []
    for venue in venues:
        venue_symbols = sorted(
            set(symbols) | {s for (v, s) in market.latest_snapshots.keys() if v == venue}
        )
        adapter_markets: list[ExchangeMarketRead] = []
        for symbol in venue_symbols:
            snap = market.latest_snapshots.get((venue, symbol))
            if snap:
                adapter_markets.append(
                    ExchangeMarketRead(
                        venue=snap.venue,
                        symbol=snap.symbol,
                        funding_rate=snap.funding_rate,
                        funding_rate_native=snap.funding_rate_native,
                        funding_interval_hours=snap.funding_interval_hours,
                        mark_price=snap.mark_price,
                        open_interest=snap.open_interest,
                        bid=snap.bid,
                        ask=snap.ask,
                        observed_at=snap.observed_at,
                    )
                )

        if not adapter_markets:
            async with request.app.state.session_factory() as session:
                for symbol in venue_symbols:
                    snap_model = await session.scalar(
                        select(FundingSnapshot)
                        .where(FundingSnapshot.venue == venue, FundingSnapshot.symbol == symbol)
                        .order_by(FundingSnapshot.observed_at.desc())
                        .limit(1)
                    )
                    if snap_model:
                        adapter_markets.append(ExchangeMarketRead.model_validate(snap_model))

        results.append(
            ExchangeSummaryRead(
                id=venue,
                name=EXCHANGE_NAMES.get(venue, venue.capitalize()),
                status="active" if adapter_markets else "standby",
                markets_count=len(adapter_markets),
                symbols=venue_symbols,
                markets=adapter_markets,
            )
        )
    return results
