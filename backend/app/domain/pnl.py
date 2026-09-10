from dataclasses import dataclass


@dataclass(frozen=True)
class PnlBreakdown:
    gross_pnl_usd: float
    paid_fees_usd: float
    net_pnl_usd: float
    estimated_close_fee_usd: float
    estimated_net_pnl_if_closed_usd: float
    estimated_net_proceeds_usd: float


def basis_bps(long_price: float, short_price: float) -> float:
    average = (long_price + short_price) / 2
    return (short_price - long_price) / average * 10_000 if average else 0.0


def basis_pnl(
    long_entry: float,
    short_entry: float,
    long_current: float,
    short_current: float,
    size_usd: float,
) -> float:
    if not long_entry or not short_entry:
        return 0.0
    long_move = (long_current - long_entry) / long_entry
    short_move = (short_entry - short_current) / short_entry
    return (long_move + short_move) * size_usd


def position_pnl(position, opportunity=None) -> PnlBreakdown:
    """Use one definition for gross, paid-fee net, and close-now estimates."""
    basis = position.basis_pnl_usd or 0.0
    if opportunity is not None and position.status == "open":
        long_move = (
            opportunity.long_mark_price - position.long_entry_price
        ) / position.long_entry_price
        short_move = (
            position.short_entry_price - opportunity.short_mark_price
        ) / position.short_entry_price
        basis = (long_move + short_move) * (position.size_usd or 0.0)
    gross = (
        position.realized_basis_pnl_usd or 0.0
    ) + (position.funding_pnl_usd or 0.0) + basis
    paid_fees = position.fees_usd or 0.0
    net = gross - paid_fees
    close_fee = 0.0
    if position.status == "open" and opportunity is not None:
        leg_size = position.leg_size_usd or position.size_usd
        close_fee = leg_size * opportunity.exit_fee_bps / 10_000
    estimated = net - close_fee
    margin = 2 * (
        position.margin_per_leg_usd
        if position.margin_per_leg_usd is not None
        else (position.leg_size_usd or position.size_usd) / max(position.leverage or 1.0, 1.0)
    )
    return PnlBreakdown(
        gross_pnl_usd=gross,
        paid_fees_usd=paid_fees,
        net_pnl_usd=net,
        estimated_close_fee_usd=close_fee,
        estimated_net_pnl_if_closed_usd=estimated,
        estimated_net_proceeds_usd=margin + estimated,
    )
