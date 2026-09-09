from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FundingPayment, Position


async def apply_confirmed_metrics(session: AsyncSession, position: Position) -> None:
    """Refresh P&L and risk metrics from confirmed payments only."""
    statement = (
        select(FundingPayment)
        .where(
            FundingPayment.position_id == position.id,
            FundingPayment.rate_source == "exchange_history",
            FundingPayment.settlement_type == "confirmed",
        )
        .order_by(FundingPayment.cycle_at.desc(), FundingPayment.id.desc())
    )
    payments = list((await session.execute(statement)).scalars())
    if not payments:
        _clear_metrics(position)
        return

    position.settled_long_funding_pnl_usd = sum(
        payment.long_payment_usd for payment in payments
    )
    position.settled_short_funding_pnl_usd = sum(
        payment.short_payment_usd for payment in payments
    )
    position.settled_funding_pnl_usd = (
        position.settled_long_funding_pnl_usd
        + position.settled_short_funding_pnl_usd
    )
    position.long_funding_pnl_usd = position.settled_long_funding_pnl_usd
    position.short_funding_pnl_usd = position.settled_short_funding_pnl_usd
    position.funding_pnl_usd = position.settled_funding_pnl_usd

    latest = payments[0]
    latest_net_rate = latest.short_rate - latest.long_rate
    position.last_net_apr_pct = latest_net_rate * 24 * 365 * 100
    position.last_long_funding_rate = latest.long_rate
    position.last_short_funding_rate = latest.short_rate
    position.last_rate_observed_at = _aware(latest.cycle_at)

    negative_hours = 0
    previous_cycle = None
    for payment in payments:
        cycle = _aware(payment.cycle_at)
        if previous_cycle is not None:
            gap_seconds = (previous_cycle - cycle).total_seconds()
            if abs(gap_seconds - 3600) > 60:
                break
        if payment.net_payment_usd >= 0:
            break
        negative_hours += 1
        previous_cycle = cycle
    position.negative_hours = negative_hours
    position.last_negative_hour = (
        _aware(latest.cycle_at).strftime("%Y-%m-%dT%H") if negative_hours else None
    )


def _clear_metrics(position: Position) -> None:
    position.settled_long_funding_pnl_usd = 0.0
    position.settled_short_funding_pnl_usd = 0.0
    position.settled_funding_pnl_usd = 0.0
    position.long_funding_pnl_usd = 0.0
    position.short_funding_pnl_usd = 0.0
    position.funding_pnl_usd = 0.0
    position.last_net_apr_pct = None
    position.last_long_funding_rate = None
    position.last_short_funding_rate = None
    position.last_rate_observed_at = None
    position.negative_hours = 0
    position.last_negative_hour = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
