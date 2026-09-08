import time

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app.services.diagnostics import (
    ALLOWED_REDIS_COMMANDS,
    PostgresDiagnosticsRead,
    PostgresQueryRequest,
    PostgresQueryResponse,
    RedisCommandRequest,
    RedisCommandResponse,
    RedisDiagnosticsRead,
    decode_redis_val,
    emulate_memory_redis,
    serialize_value,
    validate_readonly_sql,
)

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])


@router.get("/postgres", response_model=PostgresDiagnosticsRead)
async def diagnose_postgres(request: Request) -> PostgresDiagnosticsRead:
    session_factory = request.app.state.session_factory
    engine = getattr(request.app.state, "db_engine", None)
    dialect = engine.dialect.name if engine else "unknown"
    start = time.perf_counter()
    table_counts: dict[str, int] = {}
    version_str: str | None = None

    try:
        async with session_factory() as session:
            v_query = "SELECT version()" if dialect == "postgresql" else "SELECT sqlite_version()"
            res = await session.execute(text(v_query))
            version_row = res.first()
            if version_row:
                version_str = str(version_row[0])

            for tbl in ("funding_snapshots", "positions", "trade_logs", "system_settings"):
                try:
                    c_res = await session.execute(text(f"SELECT count(*) FROM {tbl}"))
                    row = c_res.first()
                    if row:
                        table_counts[tbl] = int(row[0])
                except Exception:
                    pass
            await session.rollback()

        latency_ms = (time.perf_counter() - start) * 1000
        return PostgresDiagnosticsRead(
            status="ok",
            latency_ms=round(latency_ms, 2),
            dialect=dialect,
            version=version_str,
            table_counts=table_counts,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return PostgresDiagnosticsRead(
            status="error",
            latency_ms=round(latency_ms, 2),
            dialect=dialect,
            version=f"Connection error: {exc}",
            table_counts={},
        )


@router.post("/postgres", response_model=PostgresQueryResponse)
async def query_postgres(request: Request, body: PostgresQueryRequest) -> PostgresQueryResponse:
    validated_query = validate_readonly_sql(body.query)
    session_factory = request.app.state.session_factory
    start = time.perf_counter()
    try:
        async with session_factory() as session:
            result = await session.execute(text(validated_query))
            columns = list(result.keys()) if result.returns_rows else []
            fetched = result.fetchmany(body.limit) if result.returns_rows else []
            rows = [[serialize_value(item) for item in row] for row in fetched]
            await session.rollback()
        latency_ms = (time.perf_counter() - start) * 1000
        return PostgresQueryResponse(
            status="ok",
            latency_ms=round(latency_ms, 2),
            query=validated_query,
            columns=columns,
            rows=rows,
            row_count=len(rows),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Query execution error: {exc}",
        ) from exc


@router.get("/redis", response_model=RedisDiagnosticsRead)
async def diagnose_redis(request: Request) -> RedisDiagnosticsRead:
    cache = getattr(request.app.state, "cache", None)
    redis_client = getattr(cache, "_redis", None) if cache else None
    start = time.perf_counter()

    if redis_client:
        try:
            ping_res = await redis_client.ping()
            dbsize = await redis_client.dbsize()
            keys = await redis_client.keys("*")
            info_raw = await redis_client.info("memory")
            latency_ms = (time.perf_counter() - start) * 1000
            decoded_keys = [k.decode("utf-8") if isinstance(k, bytes) else str(k) for k in keys[:50]]
            return RedisDiagnosticsRead(
                status="ok",
                latency_ms=round(latency_ms, 2),
                mode="redis",
                ping="PONG" if ping_res else "FAILED",
                key_count=dbsize,
                keys_sample=decoded_keys,
                info=info_raw if isinstance(info_raw, dict) else None,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            return RedisDiagnosticsRead(
                status="error",
                latency_ms=round(latency_ms, 2),
                mode="redis",
                ping=f"Error: {exc}",
                key_count=0,
                keys_sample=[],
                info=None,
            )
    else:
        mem = getattr(cache, "_memory", {}) if cache else {}
        latency_ms = (time.perf_counter() - start) * 1000
        return RedisDiagnosticsRead(
            status="ok",
            latency_ms=round(latency_ms, 2),
            mode="in_memory",
            ping="PONG",
            key_count=len(mem),
            keys_sample=list(mem.keys())[:50],
            info={"type": "in_memory_cache", "total_keys": len(mem)},
        )


@router.post("/redis", response_model=RedisCommandResponse)
async def query_redis(request: Request, body: RedisCommandRequest) -> RedisCommandResponse:
    cmd = body.command.strip().upper()
    if cmd not in ALLOWED_REDIS_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Command '{body.command}' is not permitted. Only read-only inspection commands are allowed.",
        )
    cache = getattr(request.app.state, "cache", None)
    redis_client = getattr(cache, "_redis", None) if cache else None
    start = time.perf_counter()

    if redis_client:
        try:
            raw_res = await redis_client.execute_command(cmd, *body.args)
            result = decode_redis_val(raw_res)
            latency_ms = (time.perf_counter() - start) * 1000
            return RedisCommandResponse(
                status="ok",
                latency_ms=round(latency_ms, 2),
                mode="redis",
                command=cmd,
                args=body.args,
                result=result,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Redis error: {exc}") from exc
    else:
        mem = getattr(cache, "_memory", {}) if cache else {}
        result = emulate_memory_redis(cmd, body.args, mem)
        latency_ms = (time.perf_counter() - start) * 1000
        return RedisCommandResponse(
            status="ok",
            latency_ms=round(latency_ms, 2),
            mode="in_memory",
            command=cmd,
            args=body.args,
            result=result,
        )
