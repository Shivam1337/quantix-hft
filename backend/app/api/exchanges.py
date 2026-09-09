from fastapi import APIRouter, Request

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
        await market.load_cached_snapshots()
    if not market.latest_snapshots:
        try:
            await market.list_opportunities()
        except Exception:
            pass

    settlements = await market.historical_service.list_settlements(limit=10_000)
    latest_settlement = {}
    for settlement in settlements:
        key = (settlement.venue.lower(), settlement.symbol.upper())
        latest_settlement.setdefault(key, settlement)

    results: list[ExchangeSummaryRead] = []
    for venue in venues:
        venue_symbols = sorted(
            set(symbols) | {s for (v, s) in market.latest_snapshots.keys() if v == venue}
        )
        adapter_markets: list[ExchangeMarketRead] = []
        for symbol in venue_symbols:
            snap = market.latest_snapshots.get((venue, symbol))
            if snap:
                settlement = latest_settlement.get((venue, symbol.upper()))
                adapter_markets.append(
                    ExchangeMarketRead(
                        venue=snap.venue,
                        symbol=snap.symbol,
                        funding_rate=settlement.funding_rate if settlement else None,
                        funding_rate_native=(
                            settlement.funding_rate_native if settlement else None
                        ),
                        funding_interval_hours=(
                            settlement.funding_interval_hours if settlement else 1.0
                        ),
                        funding_cycle_at=settlement.funding_cycle_at if settlement else None,
                        funding_rate_source=(
                            "confirmed_history" if settlement else "unavailable"
                        ),
                        mark_price=snap.mark_price,
                        open_interest=snap.open_interest,
                        bid=snap.bid,
                        ask=snap.ask,
                        observed_at=snap.observed_at,
                    )
                )

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
