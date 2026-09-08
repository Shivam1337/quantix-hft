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
        "funding_snapshots": {
            "funding_rate_native": "FLOAT",
            "funding_interval_hours": "FLOAT DEFAULT 1",
            "funding_cycle_at": "TIMESTAMP NULL",
        },
        "positions": {
            "entry_fee_usd": "FLOAT DEFAULT 0",
            "exit_fee_usd": "FLOAT DEFAULT 0",
            "fees_usd": "FLOAT DEFAULT 0",
            "open_reason": "TEXT NULL",
            "leg_size_usd": "FLOAT NULL",
        },
        "trade_logs": {
            "phase": "VARCHAR(12) DEFAULT 'open'",
            "fee_bps": "FLOAT DEFAULT 0",
            "fee_usd": "FLOAT DEFAULT 0",
        },
    }
    inspector = inspect(connection)
    for table, columns in additions.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
