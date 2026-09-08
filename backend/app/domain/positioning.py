from collections.abc import Iterable
from dataclasses import replace

from app.domain.types import OpportunityData


def opportunity_for_position(
    position, opportunities: Iterable[OpportunityData]
) -> OpportunityData | None:
    """Return market data in the position's original leg direction."""
    values = list(opportunities)
    by_id = {item.id: item for item in values}
    exact = by_id.get(position.opportunity_id)
    if exact is not None:
        return exact

    reverse_id = f"{position.symbol}:{position.short_venue}:{position.long_venue}"
    reverse = by_id.get(reverse_id)
    if reverse is None:
        return None
    return orient_opportunity(reverse, position.long_venue, position.short_venue)


def orient_opportunity(
    opportunity: OpportunityData, long_venue: str, short_venue: str
) -> OpportunityData:
    """Rebuild a reverse-ranked opportunity for an existing position."""
    if (
        opportunity.long_venue.lower() == long_venue.lower()
        and opportunity.short_venue.lower() == short_venue.lower()
    ):
        return opportunity

    long_rate = opportunity.short_funding_rate
    short_rate = opportunity.long_funding_rate
    gross = short_rate - long_rate
    fee_drag = opportunity.round_trip_fee_bps / 10_000 / (24 * 30)
    net_hourly = gross - fee_drag
    round_trip_fee = opportunity.round_trip_fee_bps / 10_000
    return replace(
        opportunity,
        id=f"{opportunity.symbol}:{long_venue}:{short_venue}",
        long_venue=long_venue,
        short_venue=short_venue,
        long_funding_rate=long_rate,
        short_funding_rate=short_rate,
        gross_hourly_rate=gross,
        net_hourly_rate=net_hourly,
        net_apr_pct=net_hourly * 24 * 365 * 100,
        basis_bps=-opportunity.basis_bps,
        long_order_type=opportunity.short_order_type,
        short_order_type=opportunity.long_order_type,
        long_mark_price=opportunity.short_mark_price,
        short_mark_price=opportunity.long_mark_price,
        fee_breakeven_hours=round_trip_fee / gross if gross > 0 else None,
        historical_3d_apr_pct=(
            -abs(opportunity.historical_3d_apr_pct)
            if opportunity.historical_3d_apr_pct is not None
            else None
        ),
    )
