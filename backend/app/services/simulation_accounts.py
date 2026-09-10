from datetime import datetime, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.pnl import position_pnl
from app.models import Position, SimulationAccount, SimulationRun


async def ensure_account(
    session: AsyncSession, initial_balance: float, leverage: float
) -> SimulationAccount:
    account = await session.get(SimulationAccount, 1)
    if account is None:
        account = SimulationAccount(
            id=1,
            initial_balance=initial_balance,
            current_balance=initial_balance,
            allocated_balance=0.0,
            total_realized_pnl=0.0,
            leverage=leverage,
        )
        session.add(account)
        await session.flush()
    elif (
        account.initial_balance in (50.0, 10_000.0)
        and account.current_balance == account.initial_balance
        and account.allocated_balance == 0.0
        and account.total_realized_pnl == 0.0
    ):
        account.initial_balance = initial_balance
        account.current_balance = initial_balance
        account.leverage = leverage
    if account.run_id is None or await session.get(SimulationRun, account.run_id) is None:
        run = await new_run(session, account)
        account.run_id = run.id
    await session.execute(
        update(Position)
        .where(Position.simulation_run_id.is_(None))
        .values(simulation_run_id=account.run_id)
    )
    return account


async def new_run(session: AsyncSession, account: SimulationAccount) -> SimulationRun:
    latest = await session.scalar(select(func.max(SimulationRun.run_number))) or 0
    now = datetime.now(timezone.utc)
    run = SimulationRun(
        run_number=latest + 1,
        status="active",
        initial_balance=account.initial_balance,
        current_balance=account.current_balance,
        allocated_balance=account.allocated_balance,
        total_realized_pnl=account.total_realized_pnl,
        leverage=account.leverage,
        started_at=now,
    )
    session.add(run)
    await session.flush()
    return run


async def reconcile_position(
    session: AsyncSession, account: SimulationAccount, position: Position
) -> None:
    run = await session.get(SimulationRun, position.simulation_run_id or account.run_id)
    if run is None:
        return
    net_pnl = position_pnl(position).net_pnl_usd
    previous = position.accounted_net_pnl_usd or 0.0
    delta = net_pnl - previous
    if abs(delta) > 1e-12:
        run.current_balance += delta
        run.total_realized_pnl += delta
        position.accounted_net_pnl_usd = net_pnl
    if run.id == account.run_id:
        copy_run_to_account(run, account)


async def sync_account(session: AsyncSession, account: SimulationAccount) -> None:
    active = list(
        (
            await session.execute(
                select(Position).where(
                    Position.status == "open",
                    or_(
                        Position.simulation_run_id == account.run_id,
                        Position.simulation_run_id.is_(None),
                    ),
                )
            )
        ).scalars()
    )
    account.allocated_balance = sum(2 * (p.margin_per_leg_usd or 0) for p in active)
    run = await session.get(SimulationRun, account.run_id)
    if run:
        copy_account_to_run(account, run)


def copy_account_to_run(account: SimulationAccount, run: SimulationRun) -> None:
    run.initial_balance = account.initial_balance
    run.current_balance = account.current_balance
    run.allocated_balance = account.allocated_balance
    run.total_realized_pnl = account.total_realized_pnl
    run.leverage = account.leverage


def copy_run_to_account(run: SimulationRun, account: SimulationAccount) -> None:
    account.current_balance = run.current_balance
    account.total_realized_pnl = run.total_realized_pnl
    account.allocated_balance = run.allocated_balance
