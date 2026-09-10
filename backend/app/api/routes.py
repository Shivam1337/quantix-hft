from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.position_schemas import FundingPendingCycleRead, PositionRead, to_position_read
from app.domain.entry import entry_rejection_reason
from app.domain.positioning import opportunity_for_position
from app.models import FundingPendingCycle, Position, TradeLog
from app.schemas import (
    ClosePositionRequest,
    FundingSettlementRead,
    HealthRead,
    OpenPositionRequest,
    OpportunityRead,
    RefreshRead,
    SettingsRead,
    SettingsUpdate,
    SimulationAccountRead,
    SimulationResetResponse,
    TradeLogRead,
)

router = APIRouter(prefix="/api/v1")


def _opportunity_read(item, settings) -> OpportunityRead:
    return OpportunityRead(
        **{key: value for key, value in item.__dict__.items() if not key.startswith("_")},
        eligible=entry_rejection_reason(item, settings) is None,
    )


@router.get("/health", response_model=HealthRead)
async def health(request: Request) -> HealthRead:
    settings = request.app.state.settings
    return HealthRead(
        status="ok",
        environment=settings.environment,
        data_mode="historical-confirmed-rates",
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
    account = await request.app.state.simulation.get_account()
    values = await request.app.state.positions.list_positions(
        active_only=active_only,
        simulation_run_id=account.run_id if active_only else None,
    )
    opportunities = await request.app.state.market.list_opportunities()
    return [
        to_position_read(value, opportunity_for_position(value, opportunities))
        for value in values
    ]


@router.get("/funding-history", response_model=list[FundingSettlementRead])
async def funding_history(
    request: Request,
    venue: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FundingSettlementRead]:
    values = await request.app.state.market.historical_service.list_settlements(
        venue=venue, symbol=symbol, limit=limit
    )
    return [FundingSettlementRead.model_validate(value) for value in values]


@router.get("/funding-pending", response_model=list[FundingPendingCycleRead])
async def funding_pending(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[FundingPendingCycleRead]:
    statement = select(FundingPendingCycle).order_by(
        FundingPendingCycle.cycle_at.desc(), FundingPendingCycle.id.desc()
    ).limit(limit)
    async with request.app.state.session_factory() as session:
        values = list((await session.execute(statement)).scalars())
    return [FundingPendingCycleRead.model_validate(value) for value in values]


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
        account = await request.app.state.simulation.get_account()
        value = await request.app.state.positions.open_position(
            opportunity, body.capital_usd, body.paper, simulation_run_id=account.run_id
        )
        await request.app.state.simulation.sync_account_state()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_position_read(value, opportunity)


@router.post("/positions/{position_id}/close", response_model=PositionRead)
async def close_position(
    request: Request, position_id: str, body: ClosePositionRequest
) -> PositionRead:
    try:
        position = await request.app.state.positions.get_position(position_id)
        if position is None:
            raise KeyError("position not found")
        opportunities = await request.app.state.market.list_opportunities()
        opportunity = opportunity_for_position(position, opportunities)
        value = await request.app.state.positions.close_position_with_market(
            position_id, opportunity, body.reason
        )
        await request.app.state.simulation.reconcile_risk_closures([position_id])
        await request.app.state.simulation.reconcile_closed_funding(
            [opportunity] if opportunity is not None else []
        )
        await request.app.state.simulation.sync_account_state()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return to_position_read(value, opportunity)


@router.get("/settings", response_model=SettingsRead)
async def get_settings(request: Request) -> SettingsRead:
    return SettingsRead.model_validate(await request.app.state.settings_service.get())


@router.patch("/settings", response_model=SettingsRead)
async def update_settings(request: Request, body: SettingsUpdate) -> SettingsRead:
    value = await request.app.state.settings_service.update(body.model_dump(exclude_unset=True))
    request.app.state.risk.config = request.app.state.risk.config.__class__(
        basis_threshold_bps=value.basis_threshold_bps,
        negative_hours_to_unwind=value.negative_hours_to_unwind,
        auto_unwind=value.auto_unwind,
    )
    request.app.state.alerts.webhook_url = value.alert_webhook_url
    return SettingsRead.model_validate(value)


@router.get("/logs", response_model=list[TradeLogRead])
async def logs(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[TradeLogRead]:
    async with request.app.state.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(TradeLog, Position.symbol)
                    .outerjoin(Position, TradeLog.position_id == Position.id)
                    .order_by(TradeLog.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )
    output: list[TradeLogRead] = []
    for log, pos_symbol in rows:
        if not log.symbol and pos_symbol:
            log.symbol = pos_symbol
        output.append(TradeLogRead.model_validate(log))
    return output


@router.post("/simulation/reset", response_model=SimulationResetResponse)
async def reset_simulation(request: Request) -> SimulationResetResponse:
    account = await request.app.state.simulation.reset_simulation()
    return SimulationResetResponse(
        status="ok",
        message=(
            "Simulation reset successfully. A new run started; prior positions, funding, "
            "settings, and exit reasons were retained."
        ),
        account=SimulationAccountRead.model_validate(account),
    )


@router.get("/simulation/account", response_model=SimulationAccountRead)
async def get_simulation_account(request: Request) -> SimulationAccountRead:
    account = await request.app.state.simulation.get_account()
    return SimulationAccountRead.model_validate(account)
