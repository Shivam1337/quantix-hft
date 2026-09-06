"""Inspect derived lead-lag state stored in PostgreSQL or local SQLite.

This tool intentionally reports only the derived tables used by the application:
chart samples, decision transitions, lead-lag events, and closed paper trades.
It does not read or expect raw exchange messages.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from typing import Any, Dict

try:
    import asyncpg
except ImportError:
    asyncpg = None

from app.config import DATABASE_URL, SQLITE_DB_PATH


def inspect_sqlite(db_path: str, limit: int) -> Dict[str, Any]:
    if db_path.startswith("sqlite:///"):
        db_path = db_path[10:]
    elif db_path.startswith("sqlite://"):
        db_path = db_path[9:]
    elif db_path.startswith("sqlite:"):
        db_path = db_path[7:]
    db_path = os.path.abspath(db_path)
    if not os.path.exists(db_path):
        return {"error": f"SQLite database file not found at {db_path}", "backend": "sqlite"}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        counts = {
            "paper_trades": cur.execute("SELECT count(*) FROM paper_trades").fetchone()[0],
            "chart_samples": cur.execute("SELECT count(*) FROM chart_samples").fetchone()[0],
            "lead_lag_events": cur.execute("SELECT count(*) FROM lead_lag_events").fetchone()[0],
            "decision_debug": cur.execute("SELECT count(*) FROM decision_debug").fetchone()[0],
        }
        recent_trades = cur.execute(
            "SELECT payload, recorded_at FROM paper_trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        latest_chart = cur.execute(
            "SELECT payload, recorded_at FROM chart_samples ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "backend": "sqlite",
            "db_path": db_path,
            "counts": counts,
            "recent_trades": [
                {"recorded_at": row["recorded_at"], "trade": json.loads(row["payload"])}
                for row in recent_trades
            ],
            "latest_chart_sample": (
                {
                    "recorded_at": latest_chart["recorded_at"],
                    "sample": json.loads(latest_chart["payload"]),
                }
                if latest_chart
                else None
            ),
            "stores_raw_incoming_messages": False,
        }
    finally:
        conn.close()


async def inspect_postgres(dsn: str, limit: int) -> Dict[str, Any]:
    if asyncpg is None:
        raise RuntimeError("asyncpg is not installed")
    connection = await asyncpg.connect(dsn=dsn, command_timeout=8)
    try:
        counts = await connection.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM paper_trades) AS paper_trades,
                (SELECT count(*) FROM chart_samples) AS chart_samples,
                (SELECT count(*) FROM lead_lag_events) AS lead_lag_events,
                (SELECT count(*) FROM decision_debug) AS decision_debug
            """
        )
        recent_trades = await connection.fetch(
            "SELECT payload::text AS payload, recorded_at FROM paper_trades ORDER BY id DESC LIMIT $1",
            limit,
        )
        latest_chart = await connection.fetchrow(
            "SELECT payload::text AS payload, recorded_at FROM chart_samples ORDER BY id DESC LIMIT 1"
        )
        return {
            "backend": "postgresql",
            "counts": dict(counts),
            "recent_trades": [
                {"recorded_at": row["recorded_at"].isoformat(), "trade": json.loads(row["payload"])}
                for row in recent_trades
            ],
            "latest_chart_sample": (
                {
                    "recorded_at": latest_chart["recorded_at"].isoformat(),
                    "sample": json.loads(latest_chart["payload"]),
                }
                if latest_chart
                else None
            ),
            "stores_raw_incoming_messages": False,
        }
    finally:
        await connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect derived lead-lag persistence state.")
    parser.add_argument("--database-url", default=DATABASE_URL, help="PostgreSQL DSN (defaults to DATABASE_URL)")
    parser.add_argument("--sqlite-path", default=None, help="Path to SQLite database file")
    parser.add_argument("--limit", type=int, default=10, help="Maximum recent paper trades to show")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    if args.sqlite_path or args.database_url.startswith("sqlite"):
        target_path = args.sqlite_path or args.database_url
        result = inspect_sqlite(target_path, args.limit)
    else:
        try:
            result = asyncio.run(inspect_postgres(args.database_url, args.limit))
        except Exception as error:
            if os.path.exists(SQLITE_DB_PATH):
                result = inspect_sqlite(SQLITE_DB_PATH, args.limit)
                result["postgres_unavailable_reason"] = f"{type(error).__name__}: {error}"
            else:
                raise

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

