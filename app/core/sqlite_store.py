"""Asynchronous SQLite persistence for derived experiment state for local development.

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
import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import aiosqlite
except ImportError:  # pragma: no cover
    aiosqlite = None

from app.config import (
    GRACEFUL_SHUTDOWN_SECONDS,
    POSTGRES_CHART_RETENTION,
    POSTGRES_QUEUE_SIZE,
    SQLITE_DB_PATH,
)

logger = logging.getLogger("app.sqlite_store")

SQLITE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lead_lag_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL DEFAULT (DATETIME('now')),
    app_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES lead_lag_runs(run_id),
    engine_trade_id INTEGER NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (DATETIME('now')),
    payload TEXT NOT NULL,
    UNIQUE (run_id, engine_trade_id)
);
CREATE INDEX IF NOT EXISTS paper_trades_recorded_at_idx ON paper_trades (recorded_at DESC);

CREATE TABLE IF NOT EXISTS chart_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES lead_lag_runs(run_id),
    recorded_at TEXT NOT NULL DEFAULT (DATETIME('now')),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chart_samples_recorded_at_idx ON chart_samples (recorded_at DESC);

CREATE TABLE IF NOT EXISTS lead_lag_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES lead_lag_runs(run_id),
    event_type TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (DATETIME('now')),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lead_lag_events_recorded_at_idx ON lead_lag_events (recorded_at DESC);

CREATE TABLE IF NOT EXISTS decision_debug (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES lead_lag_runs(run_id),
    recorded_at TEXT NOT NULL DEFAULT (DATETIME('now')),
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS decision_debug_recorded_at_idx ON decision_debug (recorded_at DESC);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
);

CREATE TABLE IF NOT EXISTS wallet_credentials (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
);
"""


class SqliteStore:
    """Queue low-volume derived records and write them to SQLite outside feed callbacks."""

    def __init__(
        self,
        db_path: str = SQLITE_DB_PATH,
        *,
        required: bool = False,
        queue_size: int = POSTGRES_QUEUE_SIZE,
        chart_retention: int = POSTGRES_CHART_RETENTION,
    ) -> None:
        # Strip sqlite:/// prefix if present
        if db_path.startswith("sqlite:///"):
            db_path = db_path[10:]
        elif db_path.startswith("sqlite://"):
            db_path = db_path[9:]
        elif db_path.startswith("sqlite:"):
            db_path = db_path[7:]

        if db_path != ":memory:":
            self.db_path = os.path.abspath(db_path)
        else:
            self.db_path = ":memory:"
        self.required = required
        self.queue_size = queue_size
        self.chart_retention = chart_retention
        self.run_id = uuid.uuid4()
        self._db: Any = None
        self._sync_conn: Optional[sqlite3.Connection] = None
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

    async def start(
        self,
        *,
        chart_limit: int,
        trade_limit: int,
        event_limit: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Create directory, tables, run record, and start the writer."""
        if self._connected:
            return await self.load_recent(
                chart_limit=chart_limit,
                trade_limit=trade_limit,
                event_limit=event_limit,
            )

        try:
            if self.db_path != ":memory:":
                os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            if aiosqlite is not None:
                self._db = await aiosqlite.connect(self.db_path)
                self._db.row_factory = aiosqlite.Row
                await self._db.execute("PRAGMA journal_mode=WAL;")
                await self._db.execute("PRAGMA synchronous=NORMAL;")
                await self._db.execute("PRAGMA foreign_keys=ON;")
                await self._db.executescript(SQLITE_SCHEMA_SQL)
                await self._db.execute(
                    "INSERT INTO lead_lag_runs (run_id, app_version) VALUES (?, ?)",
                    (str(self.run_id), "2.3.0"),
                )
                await self._db.commit()
            else:
                # Synchronous fallback via thread
                def _init_sync() -> sqlite3.Connection:
                    conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                    conn.execute("PRAGMA foreign_keys=ON;")
                    conn.executescript(SQLITE_SCHEMA_SQL)
                    conn.execute(
                        "INSERT INTO lead_lag_runs (run_id, app_version) VALUES (?, ?)",
                        (str(self.run_id), "2.3.0"),
                    )
                    conn.commit()
                    return conn

                self._sync_conn = await asyncio.to_thread(_init_sync)

            self._queue = asyncio.Queue(maxsize=self.queue_size)
            self._accepting = True
            self._connected = True
            self._worker_task = asyncio.create_task(self._writer(), name="sqlite-derived-state-writer")
            logger.info("Local SQLite dev persistence initialized at %s", self.db_path)
            return await self.load_recent(
                chart_limit=chart_limit,
                trade_limit=trade_limit,
                event_limit=event_limit,
            )
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            await self._close_db()
            if self.required:
                raise RuntimeError(
                    f"SQLite persistence failed to initialize at {self.db_path}: {error}"
                ) from error
            logger.warning("SQLite persistence is unavailable: %s", self.last_error)
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
        if not self._connected or (self._db is None and self._sync_conn is None):
            return self._empty_snapshot()

        try:
            if self._db is not None:
                async with self._db.execute(
                    "SELECT payload FROM paper_trades ORDER BY id DESC LIMIT ?",
                    (trade_limit,),
                ) as cursor:
                    trade_rows = await cursor.fetchall()
                async with self._db.execute(
                    "SELECT payload FROM chart_samples ORDER BY id DESC LIMIT ?",
                    (chart_limit,),
                ) as cursor:
                    chart_rows = await cursor.fetchall()
                async with self._db.execute(
                    """
                    SELECT payload
                    FROM lead_lag_events
                    WHERE event_type IN ('SPREAD_CLOSED', 'TIMEOUT')
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (event_limit,),
                ) as cursor:
                    event_rows = await cursor.fetchall()
                async with self._db.execute(
                    """
                    SELECT payload FROM lead_lag_events
                    WHERE event_type = 'EXECUTION_ATTEMPT'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (trade_limit,),
                ) as cursor:
                    attempt_rows = await cursor.fetchall()
                async with self._db.execute(
                    """
                    SELECT payload FROM lead_lag_events
                    WHERE event_type = 'DUAL_EXECUTION_COMPARISON'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (trade_limit,),
                ) as cursor:
                    comparison_rows = await cursor.fetchall()
            else:
                def _query_sync():
                    c = self._sync_conn.cursor()
                    t_rows = c.execute(
                        "SELECT payload FROM paper_trades ORDER BY id DESC LIMIT ?",
                        (trade_limit,),
                    ).fetchall()
                    ch_rows = c.execute(
                        "SELECT payload FROM chart_samples ORDER BY id DESC LIMIT ?",
                        (chart_limit,),
                    ).fetchall()
                    ev_rows = c.execute(
                        """
                        SELECT payload
                        FROM lead_lag_events
                        WHERE event_type IN ('SPREAD_CLOSED', 'TIMEOUT')
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (event_limit,),
                    ).fetchall()
                    at_rows = c.execute(
                        """
                        SELECT payload FROM lead_lag_events
                        WHERE event_type = 'EXECUTION_ATTEMPT'
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (trade_limit,),
                    ).fetchall()
                    cmp_rows = c.execute(
                        """
                        SELECT payload FROM lead_lag_events
                        WHERE event_type = 'DUAL_EXECUTION_COMPARISON'
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (trade_limit,),
                    ).fetchall()
                    return t_rows, ch_rows, ev_rows, at_rows, cmp_rows

                trade_rows, chart_rows, event_rows, attempt_rows, comparison_rows = await asyncio.to_thread(_query_sync)

            trades = [payload for row in trade_rows if (payload := self._decode_payload(row["payload"] if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else row[0]))]
            samples = [payload for row in reversed(chart_rows) if (payload := self._decode_payload(row["payload"] if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else row[0]))]
            events = []
            for row in event_rows:
                payload = self._decode_payload(row["payload"] if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else row[0])
                event = payload.get("event") if payload else None
                if isinstance(event, dict):
                    events.append(event)
            attempts = []
            for row in attempt_rows:
                payload = self._decode_payload(row["payload"] if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else row[0])
                attempt = payload.get("event") if payload else None
                if isinstance(attempt, dict):
                    attempts.append(attempt)
            comparisons = []
            for row in comparison_rows:
                payload = self._decode_payload(row["payload"] if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else row[0])
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
        except Exception as error:
            logger.exception("Error loading recent records from SQLite: %s", error)
            return self._empty_snapshot()

    def record_chart_sample(self, sample: Dict[str, Any]) -> None:
        self._enqueue("chart", sample)

    def record_trade(self, trade: Dict[str, Any]) -> None:
        self._enqueue("trade", trade)

    def record_event(self, event: Dict[str, Any]) -> None:
        self._enqueue("event", event)

    def record_decision(self, decision: Dict[str, Any]) -> None:
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
            except Exception as error:
                self.records_failed += 1
                self.last_error = f"{type(error).__name__}: {error}"
                logger.exception("Unable to persist derived SQLite %s record", kind)
            finally:
                self._queue.task_done()

    async def _write(self, kind: str, payload: Dict[str, Any]) -> None:
        if not self._connected or (self._db is None and self._sync_conn is None):
            raise RuntimeError("SQLite database is unavailable")
        encoded = json.dumps(payload, separators=(",", ":"), default=str)

        if self._db is not None:
            if kind == "chart":
                await self._db.execute(
                    "INSERT INTO chart_samples (run_id, payload) VALUES (?, ?)",
                    (str(self.run_id), encoded),
                )
                self._chart_writes_since_prune += 1
                if self._chart_writes_since_prune >= 250:
                    self._chart_writes_since_prune = 0
                    await self._db.execute(
                        """
                        DELETE FROM chart_samples
                        WHERE id NOT IN (
                            SELECT id FROM chart_samples ORDER BY id DESC LIMIT ?
                        )
                        """,
                        (self.chart_retention,),
                    )
                await self._db.commit()
                return
            if kind == "trade":
                engine_trade_id = int(payload.get("id", 0))
                await self._db.execute(
                    """
                    INSERT INTO paper_trades (run_id, engine_trade_id, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT (run_id, engine_trade_id) DO UPDATE SET payload = excluded.payload
                    """,
                    (str(self.run_id), engine_trade_id, encoded),
                )
                await self._db.commit()
                return
            if kind == "event":
                await self._db.execute(
                    "INSERT INTO lead_lag_events (run_id, event_type, payload) VALUES (?, ?, ?)",
                    (str(self.run_id), str(payload.get("transition", "UNKNOWN")), encoded),
                )
                await self._db.commit()
                return
            if kind == "decision":
                await self._db.execute(
                    "INSERT INTO decision_debug (run_id, payload) VALUES (?, ?)",
                    (str(self.run_id), encoded),
                )
                await self._db.commit()
                return
        else:
            # Sync fallback
            def _write_sync():
                conn = self._sync_conn
                if kind == "chart":
                    conn.execute(
                        "INSERT INTO chart_samples (run_id, payload) VALUES (?, ?)",
                        (str(self.run_id), encoded),
                    )
                    self._chart_writes_since_prune += 1
                    if self._chart_writes_since_prune >= 250:
                        self._chart_writes_since_prune = 0
                        conn.execute(
                            """
                            DELETE FROM chart_samples
                            WHERE id NOT IN (
                                SELECT id FROM chart_samples ORDER BY id DESC LIMIT ?
                            )
                            """,
                            (self.chart_retention,),
                        )
                    conn.commit()
                    return
                if kind == "trade":
                    engine_trade_id = int(payload.get("id", 0))
                    conn.execute(
                        """
                        INSERT INTO paper_trades (run_id, engine_trade_id, payload)
                        VALUES (?, ?, ?)
                        ON CONFLICT (run_id, engine_trade_id) DO UPDATE SET payload = excluded.payload
                        """,
                        (str(self.run_id), engine_trade_id, encoded),
                    )
                    conn.commit()
                    return
                if kind == "event":
                    conn.execute(
                        "INSERT INTO lead_lag_events (run_id, event_type, payload) VALUES (?, ?, ?)",
                        (str(self.run_id), str(payload.get("transition", "UNKNOWN")), encoded),
                    )
                    conn.commit()
                    return
                if kind == "decision":
                    conn.execute(
                        "INSERT INTO decision_debug (run_id, payload) VALUES (?, ?)",
                        (str(self.run_id), encoded),
                    )
                    conn.commit()
                    return

            await asyncio.to_thread(_write_sync)
            return

        raise ValueError(f"Unsupported persistence record type: {kind}")

    async def reset_simulation_data(self) -> None:
        """Deletes historical paper trades, chart samples, lead lag events, and decision debug records."""
        try:
            if self._db is not None:
                await self._db.execute("DELETE FROM paper_trades")
                await self._db.execute("DELETE FROM chart_samples")
                await self._db.execute("DELETE FROM lead_lag_events")
                await self._db.execute("DELETE FROM decision_debug")
                await self._db.commit()
            elif self._sync_conn is not None:
                def _clear_sync():
                    self._sync_conn.execute("DELETE FROM paper_trades")
                    self._sync_conn.execute("DELETE FROM chart_samples")
                    self._sync_conn.execute("DELETE FROM lead_lag_events")
                    self._sync_conn.execute("DELETE FROM decision_debug")
                    self._sync_conn.commit()
                await asyncio.to_thread(_clear_sync)
            logger.info("Cleared historical simulation records from SQLite.")
        except Exception as e:
            logger.error("Failed to clear simulation data in SQLite: %s", e)

    async def stop(self) -> None:
        """Drain accepted derived records, then close SQLite cleanly."""
        self._accepting = False
        if self._queue is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=GRACEFUL_SHUTDOWN_SECONDS)
            except asyncio.TimeoutError:
                self.last_error = "Timed out while draining derived SQLite persistence queue"
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        await self._close_db()
        self._queue = None

    async def _close_db(self) -> None:
        self._accepting = False
        self._connected = False
        if self._db is not None:
            await self._db.close()
            self._db = None
        if self._sync_conn is not None:
            await asyncio.to_thread(self._sync_conn.close)
            self._sync_conn = None

    def get_database_size_bytes(self) -> int:
        if self.db_path == ":memory:":
            return 0
        total = 0
        for path in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            if os.path.exists(path):
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
        return total

    def get_database_size_formatted(self) -> Dict[str, Any]:
        size_bytes = self.get_database_size_bytes()
        mb = size_bytes / (1024 * 1024)
        gb = size_bytes / (1024 * 1024 * 1024)
        formatted = f"{gb:.2f} GB" if gb >= 1.0 else f"{mb:.2f} MB"
        return {
            "size_bytes": size_bytes,
            "size_mb": round(mb, 2),
            "size_gb": round(gb, 4),
            "formatted": formatted,
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "backend": "sqlite",
            "db_path": self.db_path,
            "configured": bool(self.db_path),
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
            "database_size": self.get_database_size_formatted(),
        }

    def save_system_settings_sync(self, settings: Dict[str, Any], key: str = "current") -> None:
        """Synchronously persist system settings dictionary into SQLite."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        encoded = json.dumps(settings, separators=(",", ":"), default=str)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
                );
                """
            )
            conn.execute(
                """
                INSERT INTO system_settings (key, payload, updated_at)
                VALUES (?, ?, DATETIME('now'))
                ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, updated_at = DATETIME('now')
                """,
                (key, encoded),
            )

    def load_system_settings_sync(self, key: str = "current") -> Optional[Dict[str, Any]]:
        """Synchronously load system settings dictionary from SQLite."""
        if self.db_path != ":memory:" and not os.path.exists(self.db_path):
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT payload FROM system_settings WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return self._decode_payload(row[0])
                return None
        except Exception as e:
            logger.warning("Could not load system_settings from SQLite (%s): %s", self.db_path, e)
            return None

    def save_wallet_credentials_sync(self, wallet_data: Dict[str, Any], key: str = "active") -> None:
        """Synchronously persist wallet credentials dictionary into SQLite."""
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        encoded = json.dumps(wallet_data, separators=(",", ":"), default=str)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_credentials (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (DATETIME('now'))
                );
                """
            )
            conn.execute(
                """
                INSERT INTO wallet_credentials (key, payload, updated_at)
                VALUES (?, ?, DATETIME('now'))
                ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, updated_at = DATETIME('now')
                """,
                (key, encoded),
            )

    def load_wallet_credentials_sync(self, key: str = "active") -> Optional[Dict[str, Any]]:
        """Synchronously load wallet credentials dictionary from SQLite."""
        if self.db_path != ":memory:" and not os.path.exists(self.db_path):
            return None
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT payload FROM wallet_credentials WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return self._decode_payload(row[0])
                return None
        except Exception as e:
            logger.warning("Could not load wallet_credentials from SQLite (%s): %s", self.db_path, e)
            return None

    async def save_system_settings(self, settings: Dict[str, Any], key: str = "current") -> None:
        """Asynchronously persist system settings into SQLite."""
        if self._db is not None:
            encoded = json.dumps(settings, separators=(",", ":"), default=str)
            await self._db.execute(
                """
                INSERT INTO system_settings (key, payload, updated_at)
                VALUES (?, ?, DATETIME('now'))
                ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, updated_at = DATETIME('now')
                """,
                (key, encoded),
            )
            await self._db.commit()
        else:
            await asyncio.to_thread(self.save_system_settings_sync, settings, key)

    async def load_system_settings(self, key: str = "current") -> Optional[Dict[str, Any]]:
        """Asynchronously load system settings from SQLite."""
        if self._db is not None:
            async with self._db.execute(
                "SELECT payload FROM system_settings WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    raw = row["payload"] if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else row[0]
                    return self._decode_payload(raw)
                return None
        return await asyncio.to_thread(self.load_system_settings_sync, key)

    async def save_wallet_credentials(self, wallet_data: Dict[str, Any], key: str = "active") -> None:
        """Asynchronously persist wallet credentials into SQLite."""
        if self._db is not None:
            encoded = json.dumps(wallet_data, separators=(",", ":"), default=str)
            await self._db.execute(
                """
                INSERT INTO wallet_credentials (key, payload, updated_at)
                VALUES (?, ?, DATETIME('now'))
                ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, updated_at = DATETIME('now')
                """,
                (key, encoded),
            )
            await self._db.commit()
        else:
            await asyncio.to_thread(self.save_wallet_credentials_sync, wallet_data, key)

    async def load_wallet_credentials(self, key: str = "active") -> Optional[Dict[str, Any]]:
        """Asynchronously load wallet credentials from SQLite."""
        if self._db is not None:
            async with self._db.execute(
                "SELECT payload FROM wallet_credentials WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    raw = row["payload"] if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else row[0]
                    return self._decode_payload(raw)
                return None
        return await asyncio.to_thread(self.load_wallet_credentials_sync, key)
