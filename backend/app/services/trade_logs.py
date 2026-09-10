from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.execution import ExecutionResult
from app.models import TradeLog


def add_trade_logs(
    session: AsyncSession,
    position_id: str,
    result: ExecutionResult,
    symbol: str | None = None,
) -> None:
    for leg in result.legs:
        session.add(
            TradeLog(
                position_id=position_id,
                symbol=symbol or getattr(leg, "symbol", None) or None,
                venue=leg.venue,
                side=leg.side,
                order_type=leg.order_type,
                size_usd=leg.size_usd,
                price=leg.price,
                status=leg.status,
                client_order_id=leg.client_order_id,
                phase=leg.phase,
                fee_bps=leg.fee_bps,
                fee_usd=leg.fee_usd,
            )
        )
