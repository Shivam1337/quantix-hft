"""Asynchronous PostgreSQL persistence for derived experiment state only.

Incoming exchange messages are intentionally not stored. The database contains
rate-limited chart samples, decision transitions, lead-lag events, and closed
paper trades so a dashboard restart can recover useful state without retaining
raw market-message traffic.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import asyncpg
except ImportError:  # pragma: no cover - exercised only in incomplete deployments
    asyncpg = None

from app.config import (
    DATABASE_URL,
    FALLBACK_TO_SQLITE,
    GRACEFUL_SHUTDOWN_SECONDS,
    POSTGRES_CHART_RETENTION,
    POSTGRES_QUEUE_SIZE,
    POSTGRES_REQUIRED,
    SQLITE_DB_PATH,
)
from app.core.sqlite_store import SqliteStore

logger = logging.getLogger("app.postgres_store")



SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lead_lag_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    app_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES lead_lag_runs(run_id),
    engine_trade_id INTEGER NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL,
    UNIQUE (run_id, engine_trade_id)
);
CREATE INDEX IF NOT EXISTS paper_trades_recorded_at_idx ON paper_trades (recorded_at DESC);

CREATE TABLE IF NOT EXISTS chart_samples (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES lead_lag_runs(run_id),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS chart_samples_recorded_at_idx ON chart_samples (recorded_at DESC);

CREATE TABLE IF NOT EXISTS lead_lag_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES lead_lag_runs(run_id),
    event_type TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS lead_lag_events_recorded_at_idx ON lead_lag_events (recorded_at DESC);

CREATE TABLE IF NOT EXISTS decision_debug (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES lead_lag_runs(run_id),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS decision_debug_recorded_at_idx ON decision_debug (recorded_at DESC);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wallet_credentials (
    key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class PostgresStore:
    """Queue low-volume derived records and write them outside feed callbacks."""

    def __init__(
        self,
        dsn: str = DATABASE_URL,
        *,
        required: bool = POSTGRES_REQUIRED,
        fallback_to_sqlite: bool = FALLBACK_TO_SQLITE,
        sqlite_path: str = SQLITE_DB_PATH,
        queue_size: int = POSTGRES_QUEUE_SIZE,
        chart_retention: int = POSTGRES_CHART_RETENTION,
    ) -> None:
        self.dsn = dsn
        self.required = required
        self.fallback_to_sqlite = fallback_to_sqlite
        self.sqlite_path = sqlite_path
        self.queue_size = queue_size
        self.chart_retention = chart_retention
        self.run_id = uuid.uuid4()
        self._pool: Any = None
        self._sqlite_store: Optional[SqliteStore] = None
        self._queue: Optional[asyncio.Queue[Tuple[str, Dict[str, Any]]]] = None
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._accepting = False
        self._connected = False
        self._chart_writes_since_prune = 0
        self.records_enqueued = 0
        self.records_written = 0
        self.records_dropped = 0
        self.records_failed = 0
        self.last_error: Optional[str] = None

    @staticmethod
    def _is_sqlite_dsn(dsn: str) -> bool:
        if not dsn:
            return False
        return (
            dsn.startswith("sqlite:")
            or dsn.startswith("sqlite:///")
            or dsn.startswith("sqlite://")
            or dsn.endswith(".db")
            or dsn.endswith(".sqlite")
        )

    @property
    def backend_name(self) -> str:
        if self._sqlite_store is not None:
            return "SQLite (dev)"
        return "PostgreSQL"

    @staticmethod
    def _decode_payload(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    async def _start_sqlite(
        self,
        db_path: str,
        *,
        chart_limit: int,
        trade_limit: int,
        event_limit: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        self._sqlite_store = SqliteStore(
            db_path=db_path,
            required=self.required,
            queue_size=self.queue_size,
            chart_retention=self.chart_retention,
        )
        snapshot = await self._sqlite_store.start(
            chart_limit=chart_limit,
            trade_limit=trade_limit,
            event_limit=event_limit,
        )
        self._connected = self._sqlite_store._connected
        self.run_id = self._sqlite_store.run_id
        return snapshot

    async def start(
        self,
        *,
        chart_limit: int,
        trade_limit: int,
        event_limit: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Create schema, start the writer, and return a bounded dashboard snapshot."""
        if self._connected:
            return await self.load_recent(
                chart_limit=chart_limit,
                trade_limit=trade_limit,
                event_limit=event_limit,
            )

        if self._is_sqlite_dsn(self.dsn):
            return await self._start_sqlite(
                self.dsn,
                chart_limit=chart_limit,
                trade_limit=trade_limit,
                event_limit=event_limit,
            )

        if asyncpg is None:
            self.last_error = "asyncpg is not installed"
            if self.fallback_to_sqlite:
                logger.warning(
                    "asyncpg is not installed. Falling back to local SQLite for dev: %s",
                    self.sqlite_path,
                )
                return await self._start_sqlite(
                    self.sqlite_path,
                    chart_limit=chart_limit,
                    trade_limit=trade_limit,
                    event_limit=event_limit,
                )
            if self.required:
                raise RuntimeError("PostgreSQL persistence requires the asyncpg package.")
            return self._empty_snapshot()

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=1,
                max_size=3,
                command_timeout=8,
            )
            async with self._pool.acquire() as connection:
                await connection.execute(SCHEMA_SQL)
                await connection.execute(
                    "INSERT INTO lead_lag_runs (run_id, app_version) VALUES ($1, $2)",
                    self.run_id,
                    "2.3.0",
                )
            self._queue = asyncio.Queue(maxsize=self.queue_size)
            self._accepting = True
            self._connected = True
            self._worker_task = asyncio.create_task(self._writer(), name="postgres-derived-state-writer")
            return await self.load_recent(
                chart_limit=chart_limit,
                trade_limit=trade_limit,
                event_limit=event_limit,
            )
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            await self._close_pool()
            if self.fallback_to_sqlite:
                logger.warning(
                    "PostgreSQL is unavailable (%s). Falling back to local SQLite for dev: %s",
                    self.last_error,
                    self.sqlite_path,
                )
                return await self._start_sqlite(
                    self.sqlite_path,
                    chart_limit=chart_limit,
                    trade_limit=trade_limit,
                    event_limit=event_limit,
                )
            if self.required:
                raise RuntimeError(
                    "PostgreSQL is required but unavailable. Start the local database with "
                    "`docker compose up -d postgres` or set DATABASE_URL."
                ) from error
            logger.warning("PostgreSQL persistence is unavailable: %s", self.last_error)
            return self._empty_snapshot()


    @staticmethod
    def _empty_snapshot() -> Dict[str, List[Dict[str, Any]]]:
        return {
            "chart_samples": [],
            "closed_trades": [],
            "repricing_events": [],
            "execution_attempts": [],
            "execution_comparisons": [],
        }

    async def load_recent(
        self,
        *,
        chart_limit: int,
        trade_limit: int,
        event_limit: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        if self._sqlite_store is not None:
            return await self._sqlite_store.load_recent(
                chart_limit=chart_limit,
                trade_limit=trade_limit,
                event_limit=event_limit,
            )
        if not self._connected or self._pool is None:
            return self._empty_snapshot()
        async with self._pool.acquire() as connection:
            trade_rows = await connection.fetch(
                "SELECT payload::text AS payload FROM paper_trades ORDER BY id DESC LIMIT $1",
                trade_limit,
            )
            chart_rows = await connection.fetch(
                "SELECT payload::text AS payload FROM chart_samples ORDER BY id DESC LIMIT $1",
                chart_limit,
            )
            event_rows = await connection.fetch(
                """
                SELECT payload::text AS payload
                FROM lead_lag_events
                WHERE event_type IN ('SPREAD_CLOSED', 'TIMEOUT')
                ORDER BY id DESC
                LIMIT $1
                """,
                event_limit,
            )
            attempt_rows = await connection.fetch(
                """
                SELECT payload::text AS payload
                FROM lead_lag_events
                WHERE event_type = 'EXECUTION_ATTEMPT'
                ORDER BY id DESC
                LIMIT $1
                """,
                trade_limit,
            )
            comparison_rows = await connection.fetch(
                """
                SELECT payload::text AS payload
                FROM lead_lag_events
                WHERE event_type = 'DUAL_EXECUTION_COMPARISON'
                ORDER BY id DESC
                LIMIT $1
                """,
                trade_limit,
            )

        trades = [payload for row in trade_rows if (payload := self._decode_payload(row["payload"]))]
        samples = [payload for row in reversed(chart_rows) if (payload := self._decode_payload(row["payload"]))]
        events = []
        for row in event_rows:
            payload = self._decode_payload(row["payload"])
            event = payload.get("event") if payload else None
            if isinstance(event, dict):
                events.append(event)
        attempts = []
        for row in attempt_rows:
            payload = self._decode_payload(row["payload"])
            attempt = payload.get("event") if payload else None
            if isinstance(attempt, dict):
                attempts.append(attempt)
        comparisons = []
        for row in comparison_rows:
            payload = self._decode_payload(row["payload"])
            comparison = payload.get("event") if payload else None
            if isinstance(comparison, dict):
                comparisons.append(comparison)
        return {
            "chart_samples": samples,
            "closed_trades": trades,
            "repricing_events": events,
            "execution_attempts": attempts,
            "execution_comparisons": comparisons,
        }


    def record_chart_sample(self, sample: Dict[str, Any]) -> None:
        if self._sqlite_store is not None:
            self._sqlite_store.record_chart_sample(sample)
            return
        self._enqueue("chart", sample)

    def record_trade(self, trade: Dict[str, Any]) -> None:
        if self._sqlite_store is not None:
            self._sqlite_store.record_trade(trade)
            return
        self._enqueue("trade", trade)

    def record_event(self, event: Dict[str, Any]) -> None:
        if self._sqlite_store is not None:
            self._sqlite_store.record_event(event)
            return
        self._enqueue("event", event)

    def record_decision(self, decision: Dict[str, Any]) -> None:
        if self._sqlite_store is not None:
            self._sqlite_store.record_decision(decision)
            return
        self._enqueue("decision", decision)

    def _enqueue(self, kind: str, payload: Dict[str, Any]) -> None:
        if not self._accepting or self._queue is None:
            return
        try:
            self._queue.put_nowait((kind, copy.deepcopy(payload)))
            self.records_enqueued += 1
        except asyncio.QueueFull:
            self.records_dropped += 1

    async def _writer(self) -> None:
        assert self._queue is not None
        while True:
            try:
                kind, payload = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._write(kind, payload)
                self.records_written += 1
            except Exception as error:  # Keep feed processing alive if the DB has a transient failure.
                self.records_failed += 1
                self.last_error = f"{type(error).__name__}: {error}"
                logger.exception("Unable to persist derived %s record", kind)
            finally:
                self._queue.task_done()

    async def _write(self, kind: str, payload: Dict[str, Any]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgreSQL pool is unavailable")
        encoded = json.dumps(payload, separators=(",", ":"), default=str)
        async with self._pool.acquire() as connection:
            if kind == "chart":
                await connection.execute(
                    "INSERT INTO chart_samples (run_id, payload) VALUES ($1, $2::jsonb)",
                    self.run_id,
                    encoded,
                )
                self._chart_writes_since_prune += 1
                if self._chart_writes_since_prune >= 250:
                    self._chart_writes_since_prune = 0
                    await connection.execute(
                        """
                        DELETE FROM chart_samples
                        WHERE id IN (
                            SELECT id FROM chart_samples ORDER BY id DESC OFFSET $1
                        )
                        """,
                        self.chart_retention,
                    )
                return
            if kind == "trade":
                engine_trade_id = int(payload.get("id", 0))
                await connection.execute(
                    """
                    INSERT INTO paper_trades (run_id, engine_trade_id, payload)
                    VALUES ($1, $2, $3::jsonb)
                    ON CONFLICT (run_id, engine_trade_id) DO UPDATE SET payload = EXCLUDED.payload
                    """,
                    self.run_id,
                    engine_trade_id,
                    encoded,
                )
                return
            if kind == "event":
                await connection.execute(
                    "INSERT INTO lead_lag_events (run_id, event_type, payload) VALUES ($1, $2, $3::jsonb)",
                    self.run_id,
                    str(payload.get("transition", "UNKNOWN")),
                    encoded,
                )
                return
            if kind == "decision":
                await connection.execute(
                    "INSERT INTO decision_debug (run_id, payload) VALUES ($1, $2::jsonb)",
                    self.run_id,
                    encoded,
                )
                return
        raise ValueError(f"Unsupported persistence record type: {kind}")

    async def reset_simulation_data(self) -> None:
        """Deletes historical paper trades, chart samples, lead lag events, and decision debug records in PostgreSQL."""
        if self._sqlite_store is not None:
            await self._sqlite_store.reset_simulation_data()
            return
        if self._pool is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute("DELETE FROM paper_trades")
                await conn.execute("DELETE FROM chart_samples")
                await conn.execute("DELETE FROM lead_lag_events")
                await conn.execute("DELETE FROM decision_debug")
            logger.info("Cleared historical simulation records from PostgreSQL.")
        except Exception as e:
            logger.error("Failed to clear simulation data in PostgreSQL: %s", e)

    async def stop(self) -> None:
        """Drain accepted derived records, then close cleanly."""
        if self._sqlite_store is not None:
            await self._sqlite_store.stop()
            self._connected = False
            return
        self._accepting = False
        if self._queue is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=GRACEFUL_SHUTDOWN_SECONDS)
            except asyncio.TimeoutError:
                self.last_error = "Timed out while draining derived persistence queue"
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        await self._close_pool()
        self._queue = None

    async def _close_pool(self) -> None:
        self._accepting = False
        self._connected = False
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get_database_size_bytes(self) -> int:
        if self._sqlite_store is not None:
            return self._sqlite_store.get_database_size_bytes()
        if not self._connected or self._pool is None:
            return 0
        try:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow("SELECT pg_database_size(current_database()) AS size_bytes")
                return int(row["size_bytes"]) if row else 0
        except Exception as error:
            logger.warning("Failed to query PostgreSQL database size: %s", error)
            return 0

    async def get_database_size_formatted(self) -> Dict[str, Any]:
        if self._sqlite_store is not None:
            res = self._sqlite_store.get_database_size_formatted()
            res["backend"] = "sqlite"
            return res
        size_bytes = await self.get_database_size_bytes()
        mb = size_bytes / (1024 * 1024)
        gb = size_bytes / (1024 * 1024 * 1024)
        formatted = f"{gb:.2f} GB" if gb >= 1.0 else f"{mb:.2f} MB"
        return {
            "backend": "postgresql",
            "size_bytes": size_bytes,
            "size_mb": round(mb, 2),
            "size_gb": round(gb, 4),
            "formatted": formatted,
        }

    def stats(self) -> Dict[str, Any]:
        if self._sqlite_store is not None:
            st = self._sqlite_store.stats()
            st["fallback_active"] = not self._is_sqlite_dsn(self.dsn)
            if self.last_error:
                st["postgres_error"] = self.last_error
            return st
        return {
            "backend": "postgresql",
            "configured": bool(self.dsn),
            "connected": self._connected,
            "required": self.required,
            "run_id": str(self.run_id),
            "queue_depth": self._queue.qsize() if self._queue is not None else 0,
            "records_enqueued": self.records_enqueued,
            "records_written": self.records_written,
            "records_dropped": self.records_dropped,
            "records_failed": self.records_failed,
            "stores_raw_incoming_messages": False,
            "last_error": self.last_error,
        }

    async def save_system_settings(self, settings: Dict[str, Any], key: str = "current") -> None:
        """Persist system settings to PostgreSQL (or SQLite fallback)."""
        if self._sqlite_store is not None:
            return await self._sqlite_store.save_system_settings(settings, key)
        if not self._connected or self._pool is None:
            return
        encoded = json.dumps(settings, separators=(",", ":"), default=str)
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO system_settings (key, payload, updated_at)
                VALUES ($1, $2::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE
                SET payload = EXCLUDED.payload, updated_at = CURRENT_TIMESTAMP
                """,
                key,
                encoded,
            )

    async def load_system_settings(self, key: str = "current") -> Optional[Dict[str, Any]]:
        """Load system settings from PostgreSQL (or SQLite fallback)."""
        if self._sqlite_store is not None:
            return await self._sqlite_store.load_system_settings(key)
        if not self._connected or self._pool is None:
            return None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT payload::text AS payload FROM system_settings WHERE key = $1",
                key,
            )
            if row:
                return self._decode_payload(row["payload"])
            return None

    async def save_wallet_credentials(self, wallet_data: Dict[str, Any], key: str = "active") -> None:
        """Persist wallet credentials to PostgreSQL (or SQLite fallback)."""
        if self._sqlite_store is not None:
            return await self._sqlite_store.save_wallet_credentials(wallet_data, key)
        if not self._connected or self._pool is None:
            return
        encoded = json.dumps(wallet_data, separators=(",", ":"), default=str)
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO wallet_credentials (key, payload, updated_at)
                VALUES ($1, $2::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE
                SET payload = EXCLUDED.payload, updated_at = CURRENT_TIMESTAMP
                """,
                key,
                encoded,
            )

    async def load_wallet_credentials(self, key: str = "active") -> Optional[Dict[str, Any]]:
        """Load wallet credentials from PostgreSQL (or SQLite fallback)."""
        if self._sqlite_store is not None:
            return await self._sqlite_store.load_wallet_credentials(key)
        if not self._connected or self._pool is None:
            return None
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT payload::text AS payload FROM wallet_credentials WHERE key = $1",
                key,
            )
            if row:
                return self._decode_payload(row["payload"])
            return None

