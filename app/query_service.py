import time
import re
import sqlparse
from typing import Dict, Any, List
from app.database import db

FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", 
    "truncate", "grant", "revoke", "copy", "vacuum", "execute", 
    "lock", "merge", "call"
}


def validate_read_only_query(sql_query: str) -> None:
    """
    Validates that the SQL query is strictly read-only (SELECT / EXPLAIN).
    Raises ValueError if query contains write or destructive operations.
    """
    cleaned = sql_query.strip()
    if not cleaned:
        raise ValueError("Query string is empty.")

    parsed = sqlparse.parse(cleaned)
    if not parsed:
        raise ValueError("Invalid SQL syntax.")

    for statement in parsed:
        # Check statement type
        stmt_type = statement.get_type()
        if stmt_type.upper() not in ("SELECT", "UNKNOWN"):
            raise ValueError(f"Statement type '{stmt_type}' is not allowed. Only SELECT queries are permitted.")

        # Inspect all tokens for dangerous keywords
        for token in statement.flatten():
            token_val = token.value.lower().strip()
            if token_val in FORBIDDEN_KEYWORDS:
                raise ValueError(f"Forbidden keyword '{token.value}' detected. Mutation operations are blocked.")


async def execute_read_only_query(sql_query: str) -> Dict[str, Any]:
    """
    Executes a validated read-only SQL query against the database.
    Enforces a read-only transaction state.
    """
    start_time = time.perf_counter()
    try:
        validate_read_only_query(sql_query)

        # For PostgreSQL, prepend read-only transaction constraint if possible
        if db.is_postgres and db.pg_pool:
            async with db.pg_pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    records = await conn.fetch(sql_query)
                    elapsed = (time.perf_counter() - start_time) * 1000.0
                    if not records:
                        return {
                            "success": True,
                            "columns": [],
                            "rows": [],
                            "row_count": 0,
                            "execution_time_ms": round(elapsed, 2)
                        }
                    columns = list(records[0].keys())
                    rows = [dict(r) for r in records]
                    return {
                        "success": True,
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                        "execution_time_ms": round(elapsed, 2)
                    }
        else:
            # SQLite / Local mode
            rows = await db.fetch(sql_query)
            elapsed = (time.perf_counter() - start_time) * 1000.0
            if not rows:
                return {
                    "success": True,
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "execution_time_ms": round(elapsed, 2)
                }
            columns = list(rows[0].keys())
            return {
                "success": True,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "execution_time_ms": round(elapsed, 2)
            }

    except Exception as e:
        elapsed = (time.perf_counter() - start_time) * 1000.0
        return {
            "success": False,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "execution_time_ms": round(elapsed, 2),
            "error": str(e)
        }
