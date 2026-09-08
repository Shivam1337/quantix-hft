from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.models import FundingSnapshot, TradeLog
from app.schemas import (
    ClosePositionRequest,
    FundingSnapshotRead,
    HealthRead,
    OpenPositionRequest,
    OpportunityRead,
    PositionRead,
    RefreshRead,
    SettingsRead,
    SettingsUpdate,
    SimulationAccountRead,
    SimulationResetResponse,
    SimulatorRequest,
    SimulatorResponse,
    TradeLogRead,
)

router = APIRouter(prefix="/api/v1")


def _opportunity_read(item, settings) -> OpportunityRead:
    return OpportunityRead(
        **{key: value for key, value in item.__dict__.items() if not key.startswith("_")},
        eligible=(
            item.net_apr_pct >= settings.min_apr
            and item.min_open_interest >= settings.min_open_interest
            and abs(item.basis_bps) <= settings.basis_threshold_bps
        ),
    )


@router.get("/health", response_model=HealthRead)
async def health(request: Request) -> HealthRead:
    settings = request.app.state.settings
    return HealthRead(
        status="ok",
        environment=settings.environment,
        data_mode="live-read-only",
        scheduler_enabled=settings.enable_scheduler,
        last_refresh=request.app.state.market.last_refresh,
    )


@router.get("/opportunities", response_model=list[OpportunityRead])
async def opportunities(
    request: Request,
    refresh: bool = False,
    min_apr: float | None = Query(default=None),
    min_open_interest: float | None = Query(default=None, ge=0),
    pair: str | None = Query(default=None),
) -> list[OpportunityRead]:
    market = request.app.state.market
    configured = await request.app.state.settings_service.get()
    items = await market.list_opportunities(refresh=refresh)
    apr_floor = configured.min_apr if min_apr is None else min_apr
    oi_floor = configured.min_open_interest if min_open_interest is None else min_open_interest
    return [
        _opportunity_read(item, configured)
        for item in items
        if item.net_apr_pct >= apr_floor
        and item.min_open_interest >= oi_floor
        and (not pair or {item.long_venue, item.short_venue} == set(pair.split("/")))
    ]


@router.post("/refresh", response_model=RefreshRead)
async def refresh(request: Request) -> RefreshRead:
    items = await request.app.state.market.refresh()
    return RefreshRead(count=len(items), refreshed_at=request.app.state.market.last_refresh)


@router.get("/positions", response_model=list[PositionRead])
async def positions(request: Request, active_only: bool = True) -> list[PositionRead]:
    values = await request.app.state.positions.list_positions(active_only=active_only)
    return [PositionRead.model_validate(value) for value in values]


@router.get("/funding-history", response_model=list[FundingSnapshotRead])
async def funding_history(
    request: Request,
    venue: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FundingSnapshotRead]:
    statement = select(FundingSnapshot).order_by(
        FundingSnapshot.funding_cycle_at.desc(), FundingSnapshot.observed_at.desc()
    ).limit(limit)
    if venue:
        statement = statement.where(FundingSnapshot.venue == venue.lower())
    if symbol:
        statement = statement.where(FundingSnapshot.symbol == symbol.upper())
    async with request.app.state.session_factory() as session:
        values = list((await session.execute(statement)).scalars())
    return [FundingSnapshotRead.model_validate(value) for value in values]


@router.post("/positions/open", response_model=PositionRead, status_code=status.HTTP_201_CREATED)
async def open_position(request: Request, body: OpenPositionRequest) -> PositionRead:
    market = request.app.state.market
    configured = await request.app.state.settings_service.get()
    opportunity = await market.get_opportunity(body.opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="opportunity not found; refresh market data")
    if opportunity.net_apr_pct < configured.min_apr:
        raise HTTPException(status_code=409, detail="opportunity is below the configured APR floor")
    if opportunity.min_open_interest < configured.min_open_interest:
        raise HTTPException(status_code=409, detail="opportunity is below the open-interest floor")
    if abs(opportunity.basis_bps) > configured.basis_threshold_bps:
        raise HTTPException(
            status_code=409, detail="opportunity exceeds the basis safety threshold"
        )
    try:
        value = await request.app.state.positions.open_position(
            opportunity, body.capital_usd, body.paper
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PositionRead.model_validate(value)


@router.post("/positions/{position_id}/close", response_model=PositionRead)
async def close_position(
    request: Request, position_id: str, body: ClosePositionRequest
) -> PositionRead:
    try:
        position = await request.app.state.positions.get_position(position_id)
        if position is None:
            raise KeyError("position not found")
        opportunity = await request.app.state.market.get_opportunity(position.opportunity_id)
        value = await request.app.state.positions.close_position_with_market(
            position_id, opportunity, body.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PositionRead.model_validate(value)


@router.post("/simulator", response_model=SimulatorResponse)
async def simulator(request: Request, body: SimulatorRequest) -> SimulatorResponse:
    opportunity = await request.app.state.market.get_opportunity(body.opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=404, detail="opportunity not found; refresh market data")
    hours = body.holding_days * 24
    hourly_cashflow = opportunity.gross_hourly_rate * body.capital_usd
    funding = hourly_cashflow * hours
    entry_fees = opportunity.entry_fee_bps / 10_000 * body.capital_usd
    exit_fees = opportunity.exit_fee_bps / 10_000 * body.capital_usd
    fees = entry_fees + exit_fees
    net_profit = funding - entry_fees - exit_fees
    return SimulatorResponse(
        opportunity_id=opportunity.id,
        capital_usd=body.capital_usd,
        holding_days=body.holding_days,
        projected_hourly_cashflow_usd=hourly_cashflow,
        projected_period_funding_usd=funding,
        estimated_round_trip_fees_usd=fees,
        estimated_entry_fees_usd=entry_fees,
        estimated_exit_fees_usd=exit_fees,
        projected_net_profit_usd=net_profit,
        fee_breakeven_hours=opportunity.fee_breakeven_hours,
        projected_return_pct=net_profit / body.capital_usd * 100,
    )


@router.get("/settings", response_model=SettingsRead)
async def get_settings(request: Request) -> SettingsRead:
    return SettingsRead.model_validate(await request.app.state.settings_service.get())


@router.patch("/settings", response_model=SettingsRead)
async def update_settings(request: Request, body: SettingsUpdate) -> SettingsRead:
    value = await request.app.state.settings_service.update(body.model_dump(exclude_unset=True))
    request.app.state.risk.config = request.app.state.risk.config.__class__(
        basis_threshold_bps=value.basis_threshold_bps,
        auto_unwind=value.auto_unwind,
    )
    request.app.state.alerts.webhook_url = value.alert_webhook_url
    return SettingsRead.model_validate(value)


@router.get("/logs", response_model=list[TradeLogRead])
async def logs(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[TradeLogRead]:
    async with request.app.state.session_factory() as session:
        values = list(
            (
                await session.execute(
                    select(TradeLog).order_by(TradeLog.created_at.desc()).limit(limit)
                )
            ).scalars()
        )
    return [TradeLogRead.model_validate(value) for value in values]


@router.post("/simulation/reset", response_model=SimulationResetResponse)
async def reset_simulation(request: Request) -> SimulationResetResponse:
    account = await request.app.state.simulation.reset_simulation()
    return SimulationResetResponse(
        status="ok",
        message="Simulation reset successfully. Account balance restored and trades wiped.",
        account=SimulationAccountRead.model_validate(account),
    )


@router.get("/simulation/account", response_model=SimulationAccountRead)
async def get_simulation_account(request: Request) -> SimulationAccountRead:
    account = await request.app.state.simulation.get_account()
    return SimulationAccountRead.model_validate(account)

