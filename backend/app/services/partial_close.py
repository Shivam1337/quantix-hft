from datetime import datetime, timezone

from app.domain.execution import ExecutionResult
from app.domain.pnl import basis_pnl


def apply_close_result(position, result: ExecutionResult, reason: str) -> bool:
    """Apply executed close economics and return whether the position is closed."""
    requested = position.size_usd or 0.0
    matched = result.matched_size_usd
    if matched <= 0 or requested <= 0:
        return False
    ratio = min(1.0, matched / requested)
    remaining = 1.0 - ratio
    position.realized_basis_pnl_usd = (
        (position.realized_basis_pnl_usd or 0.0)
        + _executed_basis_pnl(position, result, matched)
    )
    position.temporary_exposure_usd = (
        (position.temporary_exposure_usd or 0.0) + result.temporary_exposure_usd
    )
    position.exit_fee_usd = (position.exit_fee_usd or 0.0) + sum(
        leg.fee_usd for leg in result.legs
    )
    position.fees_usd = (position.fees_usd or 0.0) + sum(
        leg.fee_usd for leg in result.legs
    )
    if remaining <= 1e-9:
        position.basis_pnl_usd = 0.0
        position.status = "closed"
        position.closed_at = datetime.now(timezone.utc)
        position.updated_at = position.closed_at
        position.close_reason = _completed_reason(position.close_reason, reason)
        return True

    position.basis_pnl_usd = (position.basis_pnl_usd or 0.0) * remaining
    position.size_usd = requested * remaining
    if position.leg_size_usd is not None:
        position.leg_size_usd *= remaining
    if position.margin_per_leg_usd is not None:
        position.margin_per_leg_usd *= remaining
    position.close_reason = f"partial close {matched:.2f}/{requested:.2f}: {reason}"
    position.updated_at = datetime.now(timezone.utc)
    return False


def _executed_basis_pnl(position, result: ExecutionResult, matched: float) -> float:
    long_leg, short_leg = result.legs
    return basis_pnl(
        position.long_entry_price or 0.0,
        position.short_entry_price or 0.0,
        long_leg.price,
        short_leg.price,
        matched,
    )


def _completed_reason(previous: str | None, reason: str) -> str:
    if previous and previous.startswith("partial close"):
        return f"{previous}; completed close: {reason}"
    return reason
