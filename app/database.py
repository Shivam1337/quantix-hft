import os
import time
import logging
from typing import List, Dict, Any, Optional
from app.config import settings

logger = logging.getLogger("database")

# Schema DDL (PostgreSQL & SQLite compatible)
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    slug VARCHAR(255),
    volume DOUBLE PRECISION DEFAULT 0.0,
    markets_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tokens (
    token_id VARCHAR(128) PRIMARY KEY,
    event_id VARCHAR(64) REFERENCES events(event_id) ON DELETE CASCADE,
    outcome_name VARCHAR(255) NOT NULL,
    condition_id VARCHAR(128),
    latest_price DOUBLE PRECISION DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS arb_opportunities (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(64),
    event_title VARCHAR(255),
    outcomes_count INTEGER,
    basket_sum DOUBLE PRECISION,
    gross_spread DOUBLE PRECISION,
    net_spread DOUBLE PRECISION,
    actionable BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS simulated_positions (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(64),
    event_title VARCHAR(255),
    position_type VARCHAR(16),
    entry_basket DOUBLE PRECISION,
    exit_basket DOUBLE PRECISION,
    shares DOUBLE PRECISION,
    cost DOUBLE PRECISION,
    realized_pnl DOUBLE PRECISION DEFAULT 0.0,
    status VARCHAR(16) DEFAULT 'OPEN',
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS simulated_trades (
    id SERIAL PRIMARY KEY,
    position_id INTEGER REFERENCES simulated_positions(id) ON DELETE CASCADE,
    token_id VARCHAR(128),
    outcome_name VARCHAR(255),
    side VARCHAR(8),
    price DOUBLE PRECISION,
    shares DOUBLE PRECISION,
    cost DOUBLE PRECISION,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_history (
    id SERIAL PRIMARY KEY,
    cash DOUBLE PRECISION,
    locked_capital DOUBLE PRECISION,
    total_equity DOUBLE PRECISION,
    total_pnl DOUBLE PRECISION,
    open_positions INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# SQLite fallback variant (using AUTOINCREMENT instead of SERIAL and REAL instead of DOUBLE PRECISION)
SQLITE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT,
    volume REAL DEFAULT 0.0,
    markets_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tokens (
    token_id TEXT PRIMARY KEY,
    event_id TEXT,
    outcome_name TEXT NOT NULL,
    condition_id TEXT,
    latest_price REAL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS arb_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    event_title TEXT,
    outcomes_count INTEGER,
    basket_sum REAL,
    gross_spread REAL,
    net_spread REAL,
    actionable INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS simulated_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    event_title TEXT,
    position_type TEXT,
    entry_basket REAL,
    exit_basket REAL,
    shares REAL,
    cost REAL,
    realized_pnl REAL DEFAULT 0.0,
    status TEXT DEFAULT 'OPEN',
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS simulated_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    token_id TEXT,
    outcome_name TEXT,
    side TEXT,
    price REAL,
    shares REAL,
    cost REAL,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cash REAL,
    locked_capital REAL,
    total_equity REAL,
    total_pnl REAL,
    open_positions INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

class Database:
    def __init__(self):
        self.is_postgres = False
        self.pg_pool = None
        self.sqlite_conn = None

    async def initialize(self):
        """Attempts connection to PostgreSQL; falls back to SQLite if PostgreSQL is unavailable."""
        import asyncpg
        db_url = settings.DATABASE_URL

        # Convert standard postgresql:// to parameters for asyncpg
        try:
            # First try connecting to PostgreSQL
            self.pg_pool = await asyncpg.create_pool(
                db_url,
                min_size=1,
                max_size=10,
                timeout=5.0
            )
            self.is_postgres = True
            logger.info("Connected to PostgreSQL successfully.")
            async with self.pg_pool.acquire() as conn:
                await conn.execute(SCHEMA_SQL)
            logger.info("PostgreSQL schema initialized.")
        except Exception as e:
            logger.warning(f"PostgreSQL connection failed ({e}). Falling back to local SQLite database.")
            import aiosqlite
            self.is_postgres = False
            self.sqlite_path = "polymarket_sim.db"
            async with aiosqlite.connect(self.sqlite_path) as conn:
                await conn.executescript(SQLITE_SCHEMA_SQL)
                await conn.commit()
            logger.info(f"SQLite schema initialized at {self.sqlite_path}.")

    async def execute(self, query: str, *args) -> None:
        if self.is_postgres and self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                await conn.execute(query, *args)
        else:
            import aiosqlite
            # Replace $1, $2 with ? for SQLite
            sqlite_query = query
            for i in range(len(args), 0, -1):
                sqlite_query = sqlite_query.replace(f"${i}", "?")
            async with aiosqlite.connect(self.sqlite_path) as conn:
                await conn.execute(sqlite_query, args)
                await conn.commit()

    async def fetch(self, query: str, *args) -> List[Dict[str, Any]]:
        if self.is_postgres and self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch(query, *args)
                return [dict(r) for r in rows]
        else:
            import aiosqlite
            sqlite_query = query
            for i in range(len(args), 0, -1):
                sqlite_query = sqlite_query.replace(f"${i}", "?")
            async with aiosqlite.connect(self.sqlite_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(sqlite_query, args) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args) -> Optional[Dict[str, Any]]:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args) -> Any:
        row = await self.fetchrow(query, *args)
        if row:
            return list(row.values())[0]
        return None

    async def close(self):
        """Gracefully closes connection pools on container shutdown."""
        if self.is_postgres and self.pg_pool:
            await self.pg_pool.close()
            logger.info("PostgreSQL connection pool closed cleanly.")


db = Database()
