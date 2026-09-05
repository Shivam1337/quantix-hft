"""
Asynchronous PostgreSQL Database Manager for Quantix HFT.
Handles connection pooling, schema initialization, non-blocking telemetry & fill logging,
and historical data export for quantitative investigation.
"""

import os
import json
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import asyncpg

logger = logging.getLogger("quantix.db")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://quantix:quantix_secret@localhost:5432/quantix_db"
)


class DatabaseManager:
    """Manages PostgreSQL connection pool and async query execution."""

    def __init__(self, dsn: str = DATABASE_URL):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None
        self._is_connected = False

    async def connect(self):
        """Initializes asyncpg pool and ensures database tables exist."""
        try:
            self.pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=2,
                max_size=10,
                command_timeout=10
            )
            self._is_connected = True
            await self._init_schema()
            logger.info("Connected to PostgreSQL and verified schema.")
        except Exception as e:
            logger.warning(f"PostgreSQL connection failed: {e}. Running in ephemeral memory mode.")
            self._is_connected = False

    async def close(self):
        if self.pool:
            await self.pool.close()
            self._is_connected = False

    async def _init_schema(self):
        """Creates tables and indexes if they do not already exist."""
        schema_sql = """
        CREATE TABLE IF NOT EXISTS trading_sessions (
            id SERIAL PRIMARY KEY,
            coin VARCHAR(20) NOT NULL,
            start_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            end_time TIMESTAMP WITH TIME ZONE,
            initial_capital NUMERIC(14, 4) NOT NULL,
            final_equity NUMERIC(14, 4),
            net_pnl NUMERIC(14, 4),
            total_fills INT DEFAULT 0,
            config JSONB
        );

        CREATE TABLE IF NOT EXISTS execution_fills (
            id SERIAL PRIMARY KEY,
            session_id INT REFERENCES trading_sessions(id) ON DELETE CASCADE,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            coin VARCHAR(20) NOT NULL,
            side VARCHAR(25) NOT NULL,
            price NUMERIC(18, 6) NOT NULL,
            size NUMERIC(18, 6) NOT NULL,
            notional NUMERIC(18, 4) NOT NULL,
            fee NUMERIC(14, 6) NOT NULL,
            fee_type VARCHAR(10) NOT NULL,
            inventory_after NUMERIC(18, 6) NOT NULL,
            cash_after NUMERIC(18, 4) NOT NULL
        );

        CREATE TABLE IF NOT EXISTS market_telemetry (
            id BIGSERIAL PRIMARY KEY,
            session_id INT REFERENCES trading_sessions(id) ON DELETE CASCADE,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            coin VARCHAR(20) NOT NULL,
            mid_price NUMERIC(18, 6) NOT NULL,
            spread_bps NUMERIC(10, 2) NOT NULL,
            ofi NUMERIC(14, 2) NOT NULL,
            volatility_bps NUMERIC(10, 2) NOT NULL,
            equity NUMERIC(18, 4) NOT NULL,
            inventory NUMERIC(18, 6) NOT NULL,
            circuit_breaker_active BOOLEAN DEFAULT FALSE,
            circuit_breaker_reason TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_fills_session ON execution_fills(session_id);
        CREATE INDEX IF NOT EXISTS idx_telemetry_session ON market_telemetry(session_id);
        """
        async with self.pool.acquire() as conn:
            await conn.execute(schema_sql)

    async def create_session(self, coin: str, initial_capital: float, config: Dict[str, Any]) -> Optional[int]:
        """Creates a new session record and returns its ID."""
        if not self._is_connected or not self.pool:
            return None
        try:
            async with self.pool.acquire() as conn:
                session_id = await conn.fetchval(
                    """
                    INSERT INTO trading_sessions (coin, initial_capital, config)
                    VALUES ($1, $2, $3)
                    RETURNING id
                    """,
                    coin, initial_capital, json.dumps(config)
                )
                return session_id
        except Exception as e:
            logger.error(f"Failed to create trading session in DB: {e}")
            return None

    async def end_session(self, session_id: int, final_equity: float, net_pnl: float, total_fills: int):
        """Finalizes an active trading session."""
        if not self._is_connected or not self.pool or not session_id:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE trading_sessions
                    SET end_time = NOW(),
                        final_equity = $1,
                        net_pnl = $2,
                        total_fills = $3
                    WHERE id = $4
                    """,
                    final_equity, net_pnl, total_fills, session_id
                )
        except Exception as e:
            logger.error(f"Failed to finalize session {session_id}: {e}")

    async def log_fill(
        self,
        session_id: Optional[int],
        coin: str,
        side: str,
        price: float,
        size: float,
        notional: float,
        fee: float,
        fee_type: str,
        inventory_after: float,
        cash_after: float
    ):
        """Asynchronously writes a fill event to PostgreSQL."""
        if not self._is_connected or not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO execution_fills
                    (session_id, coin, side, price, size, notional, fee, fee_type, inventory_after, cash_after)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    session_id, coin, side, price, size, notional, fee, fee_type, inventory_after, cash_after
                )
        except Exception as e:
            logger.error(f"Failed to log fill in DB: {e}")

    async def log_telemetry(
        self,
        session_id: Optional[int],
        coin: str,
        mid_price: float,
        spread_bps: float,
        ofi: float,
        volatility_bps: float,
        equity: float,
        inventory: float,
        circuit_breaker_active: bool,
        circuit_breaker_reason: str
    ):
        """Asynchronously logs periodic market & portfolio telemetry."""
        if not self._is_connected or not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO market_telemetry
                    (session_id, coin, mid_price, spread_bps, ofi, volatility_bps, equity, inventory, circuit_breaker_active, circuit_breaker_reason)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    session_id, coin, mid_price, spread_bps, ofi, volatility_bps, equity, inventory, circuit_breaker_active, circuit_breaker_reason
                )
        except Exception as e:
            logger.error(f"Failed to log telemetry in DB: {e}")

    async def get_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves recent trading sessions."""
        if not self._is_connected or not self.pool:
            return []
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, coin, start_time, end_time, initial_capital, final_equity, net_pnl, total_fills, config
                    FROM trading_sessions
                    ORDER BY id DESC
                    LIMIT $1
                    """,
                    limit
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to fetch sessions: {e}")
            return []

    async def get_fills(self, session_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves execution fills, optionally filtered by session."""
        if not self._is_connected or not self.pool:
            return []
        try:
            async with self.pool.acquire() as conn:
                if session_id:
                    rows = await conn.fetch(
                        """
                        SELECT id, session_id, timestamp, coin, side, price, size, notional, fee, fee_type, inventory_after, cash_after
                        FROM execution_fills
                        WHERE session_id = $1
                        ORDER BY id DESC
                        LIMIT $2
                        """,
                        session_id, limit
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, session_id, timestamp, coin, side, price, size, notional, fee, fee_type, inventory_after, cash_after
                        FROM execution_fills
                        ORDER BY id DESC
                        LIMIT $1
                        """,
                        limit
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to fetch fills: {e}")
            return []

    async def export_session_csv(self, session_id: int) -> str:
        """Generates CSV string of all fills for a given session for post-trade analysis."""
        fills = await self.get_fills(session_id=session_id, limit=5000)
        if not fills:
            return "id,session_id,timestamp,coin,side,price,size,notional,fee,fee_type,inventory_after,cash_after\n"

        import csv
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fills[0].keys())
        writer.writeheader()
        for f in fills:
            # Format datetime
            if isinstance(f.get("timestamp"), datetime):
                f["timestamp"] = f["timestamp"].isoformat()
            writer.writerow(f)
        return output.getvalue()


# Global DB Singleton
db = DatabaseManager()
