import fnmatch
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

MUTATING_SQL_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|REPLACE|EXEC|EXECUTE|CALL|COPY|VACUUM)\b",
    re.IGNORECASE,
)
ALLOWED_SQL_PREFIXES = ("SELECT", "WITH", "SHOW", "EXPLAIN")

ALLOWED_REDIS_COMMANDS = {
    "PING", "ECHO", "INFO", "DBSIZE", "KEYS", "SCAN",
    "GET", "MGET", "STRLEN", "TYPE", "TTL", "PTTL", "EXISTS",
    "HGET", "HGETALL", "HKEYS", "HLEN", "HMGET", "HVALS", "HEXISTS",
    "LRANGE", "LLEN", "LINDEX",
    "SMEMBERS", "SCARD", "SISMEMBER", "SRANDMEMBER",
    "ZCARD", "ZRANGE", "ZREVRANGE", "ZSCORE", "ZRANK", "ZCOUNT",
    "CLIENT LIST", "TIME", "SLOWLOG",
}


class PostgresQueryRequest(BaseModel):
    query: str = Field(default="SELECT 1 as ping", description="Read-only SQL query to execute")
    limit: int = Field(default=100, ge=1, le=1000, description="Max rows to return")


class PostgresQueryResponse(BaseModel):
    status: str
    latency_ms: float
    query: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int


class PostgresDiagnosticsRead(BaseModel):
    status: str
    latency_ms: float
    dialect: str
    version: str | None = None
    table_counts: dict[str, int] = {}


class RedisCommandRequest(BaseModel):
    command: str = Field(default="PING", description="Read-only Redis command to execute")
    args: list[str] = Field(default_factory=list, description="Arguments for the command")


class RedisCommandResponse(BaseModel):
    status: str
    latency_ms: float
    mode: str
    command: str
    args: list[str]
    result: Any


class RedisDiagnosticsRead(BaseModel):
    status: str
    latency_ms: float
    mode: str
    ping: str
    key_count: int
    keys_sample: list[str]
    info: dict[str, Any] | None = None


def validate_readonly_sql(query: str) -> str:
    cleaned = re.sub(r"--.*?$|/\*.*?\*/", "", query, flags=re.DOTALL | re.MULTILINE).strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    statements = [s.strip() for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        raise HTTPException(
            status_code=400, detail="Multiple SQL statements in a single request are not permitted"
        )
    first_stmt = statements[0]
    first_word = first_stmt.split()[0].upper()
    if first_word not in ALLOWED_SQL_PREFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Statement starting with '{first_word}' is not permitted. "
                "Only read-only queries are allowed."
            ),
        )
    if MUTATING_SQL_PATTERN.search(first_stmt):
        raise HTTPException(
            status_code=400,
            detail="Query contains potentially mutating keywords and was rejected.",
        )
    return first_stmt


def serialize_value(val: Any) -> Any:
    if val is None or isinstance(val, (int, float, bool, str)):
        return val
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, (Decimal, UUID)):
        return str(val)
    if isinstance(val, (bytes, bytearray)):
        try:
            return val.decode("utf-8")
        except UnicodeDecodeError:
            return val.hex()
    return str(val)


def decode_redis_val(val: Any) -> Any:
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    if isinstance(val, list):
        return [decode_redis_val(v) for v in val]
    if isinstance(val, dict):
        return {decode_redis_val(k): decode_redis_val(v) for k, v in val.items()}
    return val


def emulate_memory_redis(cmd: str, args: list[str], memory: dict[str, str]) -> Any:
    if cmd == "PING":
        return args[0] if args else "PONG"
    if cmd == "ECHO":
        return args[0] if args else ""
    if cmd == "DBSIZE":
        return len(memory)
    if cmd == "KEYS":
        pattern = args[0] if args else "*"
        return fnmatch.filter(list(memory.keys()), pattern)
    if cmd == "GET":
        return memory.get(args[0]) if args else None
    if cmd == "MGET":
        return [memory.get(k) for k in args]
    if cmd == "EXISTS":
        return sum(1 for k in args if k in memory)
    if cmd == "TYPE":
        return "string" if args and args[0] in memory else "none"
    if cmd in ("TTL", "PTTL"):
        return -1 if args and args[0] in memory else -2
    if cmd == "INFO":
        return {"mode": "in_memory", "keys": len(memory)}
    return {"note": f"Command {cmd} acknowledged in in-memory mode", "result": None}
