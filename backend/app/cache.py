import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class Cache:
    def __init__(self, redis_url: str | None):
        self._memory: dict[str, str] = {}
        self._redis: Redis | None = (
            Redis.from_url(redis_url) if redis_url and redis_url.startswith("redis") else None
        )

    async def get_json(self, key: str) -> Any | None:
        try:
            value = await self._redis.get(key) if self._redis else self._memory.get(key)
        except Exception as exc:  # Cache failure must not stop market ingestion.
            logger.warning("cache read failed: %s", exc)
            value = self._memory.get(key)
        return json.loads(value) if value else None

    async def set_json(self, key: str, value: Any, ttl_seconds: int = 120) -> None:
        encoded = json.dumps(value)
        self._memory[key] = encoded
        try:
            if self._redis:
                await self._redis.set(key, encoded, ex=ttl_seconds)
        except Exception as exc:
            logger.warning("cache write failed: %s", exc)

    async def publish_json(self, channel: str, value: Any) -> None:
        if not self._redis:
            return
        try:
            await self._redis.publish(channel, json.dumps(value))
        except Exception as exc:
            logger.warning("cache publish failed: %s", exc)

    async def subscribe_json(self, channel: str) -> AsyncIterator[Any]:
        if not self._redis:
            if False:
                yield None
            return
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if message and message.get("type") == "message":
                    yield json.loads(message["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
