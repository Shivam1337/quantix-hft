import asyncio
from datetime import datetime, timezone
import logging
import os

import psutil
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import Cache
from app.schemas import SystemMetricsRead

logger = logging.getLogger(__name__)


def format_bytes(num_bytes: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def _read_system_load() -> dict:
    cpu_pct = psutil.cpu_percent(interval=None)
    vm = psutil.virtual_memory()
    root_path = os.path.abspath(os.sep)
    disk = psutil.disk_usage(root_path)
    proc = psutil.Process()
    proc_mem = proc.memory_info()
    return {
        "system_cpu_pct": round(float(cpu_pct), 1),
        "system_ram_pct": round(float(vm.percent), 1),
        "system_ram_used_gb": round(float(vm.used) / (1024**3), 2),
        "system_ram_total_gb": round(float(vm.total) / (1024**3), 2),
        "system_disk_pct": round(float(disk.percent), 1),
        "system_disk_used_gb": round(float(disk.used) / (1024**3), 2),
        "system_disk_total_gb": round(float(disk.total) / (1024**3), 2),
        "process_cpu_pct": round(float(proc.cpu_percent(interval=None)), 1),
        "process_ram_mb": round(float(proc_mem.rss) / (1024**2), 1),
        "process_ram_pct": round(float(proc.memory_percent()), 2),
    }


async def _query_database_size(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int | None, str]:
    try:
        async with session_factory() as session:
            bind = session.bind or session.get_bind()
            dialect_name = bind.dialect.name
            if dialect_name == "postgresql":
                result = await session.execute(text("SELECT pg_database_size(current_database())"))
                size = result.scalar()
                if size is not None:
                    size_int = int(size)
                    return size_int, format_bytes(size_int)
            elif dialect_name == "sqlite":
                res_count = await session.execute(text("PRAGMA page_count"))
                res_size = await session.execute(text("PRAGMA page_size"))
                count = res_count.scalar() or 0
                page_sz = res_size.scalar() or 0
                size_int = int(count * page_sz)
                return size_int, format_bytes(size_int)
    except Exception as exc:
        logger.warning("database size query failed: %s", exc)
    return None, "N/A"


async def _query_redis_size(cache: Cache | None) -> tuple[int | None, str]:
    if not cache:
        return None, "N/A"
    if cache._redis:
        try:
            info = await cache._redis.info("memory")
            used = info.get("used_memory")
            used_human = info.get("used_memory_human")
            size_int = int(used) if used is not None else None
            return size_int, used_human or (format_bytes(size_int) if size_int else "N/A")
        except Exception as exc:
            logger.warning("redis memory query failed: %s", exc)
            return None, "N/A"
    # Local fallback
    keys_count = len(cache._memory)
    mem_bytes = sum(len(k) + len(v) for k, v in cache._memory.items())
    return mem_bytes, f"{format_bytes(mem_bytes)} ({keys_count} keys)"


async def collect_system_metrics(
    session_factory: async_sessionmaker[AsyncSession],
    cache: Cache | None,
) -> SystemMetricsRead:
    metrics = await asyncio.to_thread(_read_system_load)
    pg_bytes, pg_human = await _query_database_size(session_factory)
    redis_bytes, redis_human = await _query_redis_size(cache)
    return SystemMetricsRead(
        **metrics,
        postgres_size_bytes=pg_bytes,
        postgres_size_human=pg_human,
        redis_size_bytes=redis_bytes,
        redis_size_human=redis_human,
        timestamp=datetime.now(timezone.utc),
    )
