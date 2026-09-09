from collections.abc import AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base


def create_database(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_async_engine(
        database_url, echo=False, pool_pre_ping=True, connect_args=connect_args
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def initialize_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_add_fee_columns)


def _add_fee_columns(connection) -> None:
    additions = {
        "positions": {
            "entry_fee_usd": "FLOAT DEFAULT 0",
            "exit_fee_usd": "FLOAT DEFAULT 0",
            "fees_usd": "FLOAT DEFAULT 0",
            "open_reason": "TEXT NULL",
            "leg_size_usd": "FLOAT NULL",
            "long_funding_pnl_usd": "FLOAT DEFAULT 0",
            "short_funding_pnl_usd": "FLOAT DEFAULT 0",
            "last_funding_cycle": "TIMESTAMP NULL",
            "current_long_price": "FLOAT NULL",
            "current_short_price": "FLOAT NULL",
            "current_basis_bps": "FLOAT NULL",
            "settled_funding_pnl_usd": "FLOAT DEFAULT 0",
            "settled_long_funding_pnl_usd": "FLOAT DEFAULT 0",
            "settled_short_funding_pnl_usd": "FLOAT DEFAULT 0",
            "accrued_funding_pnl_usd": "FLOAT DEFAULT 0",
            "accrued_long_funding_pnl_usd": "FLOAT DEFAULT 0",
            "accrued_short_funding_pnl_usd": "FLOAT DEFAULT 0",
            "accrual_started_at": "TIMESTAMP NULL",
            "entry_net_apr_pct": "FLOAT NULL",
            "entry_historical_apr_pct": "FLOAT NULL",
            "entry_long_funding_rate": "FLOAT NULL",
            "entry_short_funding_rate": "FLOAT NULL",
            "entry_rate_observed_at": "TIMESTAMP NULL",
            "last_net_apr_pct": "FLOAT NULL",
            "last_long_funding_rate": "FLOAT NULL",
            "last_short_funding_rate": "FLOAT NULL",
            "last_rate_observed_at": "TIMESTAMP NULL",
        },
        "trade_logs": {
            "phase": "VARCHAR(12) DEFAULT 'open'",
            "fee_bps": "FLOAT DEFAULT 0",
            "fee_usd": "FLOAT DEFAULT 0",
        },
        "system_settings": {
            "entry_min_history_snapshots": "INTEGER DEFAULT 6",
            "entry_min_spread_stability_pct": "FLOAT DEFAULT 60",
        },
        "funding_payments": {
            # A pre-policy row has no proof of exchange confirmation. Quarantine
            # it when these provenance columns are introduced instead of giving
            # it the strict current-row defaults used by fresh databases.
            "settlement_type": "VARCHAR(24) DEFAULT 'legacy_unconfirmed'",
            "rate_source": "VARCHAR(32) DEFAULT 'legacy_unconfirmed'",
        },
    }
    inspector = inspect(connection)
    for table, columns in additions.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))

    connection.execute(
        text(
            "UPDATE positions SET settled_long_funding_pnl_usd = COALESCE("
            "(SELECT SUM(long_payment_usd) FROM funding_payments "
            "WHERE funding_payments.position_id = positions.id "
            "AND funding_payments.rate_source = 'exchange_history' "
            "AND funding_payments.settlement_type = 'confirmed'), 0)"
        )
    )
    connection.execute(
        text(
            "UPDATE positions SET settled_short_funding_pnl_usd = COALESCE("
            "(SELECT SUM(short_payment_usd) FROM funding_payments "
            "WHERE funding_payments.position_id = positions.id "
            "AND funding_payments.rate_source = 'exchange_history' "
            "AND funding_payments.settlement_type = 'confirmed'), 0)"
        )
    )
    connection.execute(
        text(
            "UPDATE positions SET settled_funding_pnl_usd = "
            "COALESCE(settled_long_funding_pnl_usd, 0) + "
            "COALESCE(settled_short_funding_pnl_usd, 0)"
        )
    )
    connection.execute(
        text(
            "UPDATE positions SET long_funding_pnl_usd = "
            "COALESCE(settled_long_funding_pnl_usd, 0), "
            "short_funding_pnl_usd = COALESCE(settled_short_funding_pnl_usd, 0), "
            "funding_pnl_usd = COALESCE(settled_funding_pnl_usd, 0)"
        )
    )
    connection.execute(
        text(
            "UPDATE positions SET accrued_long_funding_pnl_usd = 0, "
            "accrued_short_funding_pnl_usd = 0, accrued_funding_pnl_usd = 0"
        )
    )
    connection.execute(
        text(
            "UPDATE simulation_account SET total_realized_pnl = COALESCE(("
            "SELECT SUM(funding_pnl_usd + basis_pnl_usd - fees_usd) FROM positions "
            "WHERE status = 'closed'), 0), current_balance = initial_balance + "
            "COALESCE((SELECT SUM(funding_pnl_usd + basis_pnl_usd - fees_usd) "
            "FROM positions WHERE status = 'closed'), 0), allocated_balance = "
            "COALESCE((SELECT SUM(2 * COALESCE(leg_size_usd, size_usd)) FROM positions "
            "WHERE status = 'open'), 0)"
        )
    )


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
