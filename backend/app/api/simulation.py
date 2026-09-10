from fastapi import APIRouter, HTTPException, Request

from app.schemas import SimulatorRequest, SimulatorResponse

router = APIRouter(prefix="/api/v1")


@router.post("/simulator", response_model=SimulatorResponse)
async def simulator(request: Request, body: SimulatorRequest) -> SimulatorResponse:
    opportunity = await request.app.state.market.get_opportunity(body.opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="opportunity not found; refresh market data")
    hours = body.holding_days * 24
    leg_margin = body.capital_usd / 2.0
    leg_notional = leg_margin * body.leverage
    execution = request.app.state.positions.execution
    paper_config = execution.paper_config
    post_only_legs = sum(
        order_type == "post_only_limit"
        for order_type in (opportunity.long_order_type, opportunity.short_order_type)
    )
    expected_fill_ratio = paper_config.post_only_fill_ratio ** post_only_legs
    expected_notional = leg_notional * expected_fill_ratio
    hourly_cashflow = opportunity.gross_hourly_rate * expected_notional
    funding = hourly_cashflow * hours
    entry_fees = opportunity.entry_fee_bps / 10_000 * expected_notional
    exit_fees = opportunity.exit_fee_bps / 10_000 * expected_notional
    fees = entry_fees + exit_fees
    market_legs = 2 - post_only_legs
    slippage = expected_notional * paper_config.slippage_bps / 10_000 * market_legs * 2
    net_profit = funding - fees - slippage
    return SimulatorResponse(
        opportunity_id=opportunity.id,
        capital_usd=body.capital_usd,
        holding_days=body.holding_days,
        leverage=body.leverage,
        leg_margin_usd=leg_margin,
        leg_notional_usd=leg_notional,
        expected_filled_leg_notional_usd=expected_notional,
        expected_fill_ratio=expected_fill_ratio,
        position_notional_usd=leg_notional * 2.0,
        projected_hourly_cashflow_usd=hourly_cashflow,
        projected_period_funding_usd=funding,
        estimated_round_trip_fees_usd=fees,
        estimated_entry_fees_usd=entry_fees,
        estimated_exit_fees_usd=exit_fees,
        estimated_slippage_usd=slippage,
        projected_net_profit_usd=net_profit,
        fee_breakeven_hours=opportunity.fee_breakeven_hours,
        projected_return_pct=net_profit / body.capital_usd * 100,
    )
