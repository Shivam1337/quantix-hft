"""Durable, secret-free intent journal for live Lighter orders."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import SQLITE_DB_PATH


TERMINAL_STATES = frozenset({"TERMINAL", "SUBMISSION_FAILED"})
# Lighter's Windows signer declares ClientOrderIndex as ``ctypes.c_longlong``.
MAX_LIGHTER_CLIENT_ORDER_INDEX = 9_223_372_036_854_775_807


@dataclass(frozen=True)
class JournalOrder:
    client_order_index: int
    trade_id: int
    phase: str
    state: str
    side: str
    size_btc: float
    limit_price: float
    submitted_at: Optional[float]
    tx_hash: Optional[str]
    terminal_status: Optional[str]
    last_error: Optional[str]


class OrderJournal:
    """SQLite-WAL order state that survives process restarts without secrets."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._memory_connection: Optional[sqlite3.Connection] = None
        if path is not None and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        if path in {None, ":memory:"}:
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._initialize()

    @classmethod
    def durable_default(cls) -> "OrderJournal":
        return cls(os.getenv("ORDER_JOURNAL_DB_PATH", SQLITE_DB_PATH))

    @property
    def is_durable(self) -> bool:
        return self.path not in {None, ":memory:"}

    def reserve_intent(
        self,
        *,
        trade_id: int,
        phase: str,
        side: str,
        size_btc: float,
        limit_price: float,
        submitted_at: Optional[float] = None,
    ) -> int:
        """Commit an intent before any network submission and return a unique ID."""
        now = time.time() if submitted_at is None else float(submitted_at)
        normalized_phase = str(phase).upper()
        with self._locked_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute("SELECT COALESCE(MAX(client_order_index), 0) FROM lighter_order_journal").fetchone()[0]
            if self.is_durable:
                # A persisted microsecond clock makes collision with pre-journal
                # trade counters impractical, while retaining a monotonic retry
                # path for multiple orders in the same microsecond.
                candidate = max(int(now * 1_000_000), int(latest) + 1)
            else:
                candidate = int(trade_id) + (10_000 if normalized_phase == "EXIT" else 0)
                candidate = max(candidate, int(latest) + 1) if self._intent_exists(connection, candidate) else candidate
            if candidate > MAX_LIGHTER_CLIENT_ORDER_INDEX:
                raise RuntimeError("Lighter client order-index namespace is exhausted; do not reuse an index.")
            connection.execute(
                """
                INSERT INTO lighter_order_journal (
                    client_order_index, trade_id, phase, state, side, size_btc,
                    limit_price, created_at, updated_at, submitted_at
                ) VALUES (?, ?, ?, 'INTENT', ?, ?, ?, ?, ?, ?)
                """,
                (candidate, int(trade_id), normalized_phase, str(side).upper(), float(size_btc), float(limit_price), now, now, now),
            )
            connection.commit()
            return candidate

    def acknowledge(
        self,
        client_order_index: int,
        *,
        tx_hash: Optional[str],
        response_code: Optional[int],
        response_message: Optional[str],
    ) -> None:
        self._update(
            client_order_index,
            state="ACKNOWLEDGED",
            tx_hash=tx_hash,
            response_code=response_code,
            response_message=response_message,
        )

    def record_terminal(
        self,
        client_order_index: int,
        *,
        terminal_status: str,
        error: Optional[str] = None,
    ) -> None:
        state = "SUBMISSION_FAILED" if terminal_status == "SUBMISSION_FAILED" else "TERMINAL"
        self._update(client_order_index, state=state, terminal_status=terminal_status, last_error=error)

    def mark_position_open(self, client_order_index: int, *, terminal_status: str) -> None:
        """Keep a filled entry unresolved until its matching exit is confirmed."""
        self._update(client_order_index, state="POSITION_OPEN", terminal_status=terminal_status)

    def mark_unknown(self, client_order_index: int, *, error: str) -> None:
        self._update(client_order_index, state="UNKNOWN", last_error=error)

    def unresolved_orders(self) -> list[JournalOrder]:
        with self._locked_connection() as connection:
            rows = connection.execute(
                """
                SELECT client_order_index, trade_id, phase, state, side, size_btc,
                       limit_price, submitted_at, tx_hash, terminal_status, last_error
                FROM lighter_order_journal
                WHERE state NOT IN ('TERMINAL', 'SUBMISSION_FAILED')
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [JournalOrder(*row) for row in rows]

    def _intent_exists(self, connection: sqlite3.Connection, candidate: int) -> bool:
        return connection.execute(
            "SELECT 1 FROM lighter_order_journal WHERE client_order_index = ?", (candidate,)
        ).fetchone() is not None

    def _update(self, client_order_index: int, **values: Any) -> None:
        allowed = {"state", "tx_hash", "response_code", "response_message", "terminal_status", "last_error"}
        assignments = [(key, values[key]) for key in allowed if key in values]
        if not assignments:
            return
        sql = ", ".join(f"{key} = ?" for key, _ in assignments)
        params = [value for _, value in assignments]
        params.extend([time.time(), int(client_order_index)])
        with self._locked_connection() as connection:
            cursor = connection.execute(
                f"UPDATE lighter_order_journal SET {sql}, updated_at = ? WHERE client_order_index = ?", params
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown Lighter client_order_index: {client_order_index}")
            connection.commit()

    def _initialize(self) -> None:
        with self._locked_connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lighter_order_journal (
                    client_order_index INTEGER PRIMARY KEY,
                    trade_id INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    state TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size_btc REAL NOT NULL,
                    limit_price REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    submitted_at REAL,
                    tx_hash TEXT,
                    response_code INTEGER,
                    response_message TEXT,
                    terminal_status TEXT,
                    last_error TEXT
                )
                """
            )
            connection.commit()

    def _locked_connection(self):
        return _LockedConnection(self._lock, self.path, self._memory_connection)


class _LockedConnection:
    def __init__(self, lock: threading.RLock, path: Optional[str], memory: Optional[sqlite3.Connection]) -> None:
        self.lock, self.path, self.memory = lock, path, memory
        self.connection: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        self.connection = self.memory or sqlite3.connect(self.path or ":memory:", timeout=5.0)
        return self.connection

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        try:
            if exc_type is not None and self.connection is not None:
                self.connection.rollback()
            if self.connection is not None and self.connection is not self.memory:
                self.connection.close()
        finally:
            self.lock.release()
