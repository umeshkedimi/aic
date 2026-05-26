"""Redis cache client.

Provides async Redis connection management and health checking.
"""

import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis

from aic.config import Settings
from aic.observability.logging import get_logger

logger = get_logger(__name__)

_redis_client: redis.Redis | None = None


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    healthy: bool
    latency_ms: float | None = None
    message: str | None = None


async def init_redis(settings: Settings) -> None:
    """Initialize Redis connection pool."""
    global _redis_client

    logger.info("Initializing Redis connection", url=str(settings.redis_url).split("@")[-1])

    _redis_client = redis.from_url(
        str(settings.redis_url),
        max_connections=settings.redis_max_connections,
        socket_timeout=settings.redis_socket_timeout,
        decode_responses=True,
    )

    # Verify connection
    await _redis_client.ping()
    logger.info("Redis connection initialized")


async def close_redis() -> None:
    """Close Redis connection pool."""
    global _redis_client

    if _redis_client:
        logger.info("Closing Redis connection")
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")


def get_redis() -> redis.Redis:
    """Get the Redis client instance."""
    if _redis_client is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis_client


async def check_redis_health() -> HealthCheckResult:
    """Check Redis health with a ping command."""
    if _redis_client is None:
        return HealthCheckResult(
            healthy=False,
            message="Redis not initialized",
        )

    try:
        start = time.perf_counter()
        await _redis_client.ping()
        latency_ms = (time.perf_counter() - start) * 1000

        return HealthCheckResult(
            healthy=True,
            latency_ms=round(latency_ms, 2),
        )
    except Exception as e:
        logger.warning("Redis health check failed", error=str(e))
        return HealthCheckResult(
            healthy=False,
            message=str(e),
        )


class CacheService:
    """High-level cache service for common operations."""

    def __init__(self, prefix: str = "aic"):
        self._prefix = prefix

    def _key(self, key: str) -> str:
        """Generate prefixed cache key."""
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> str | None:
        """Get a value from cache."""
        client = get_redis()
        return await client.get(self._key(key))

    async def set(
        self,
        key: str,
        value: str,
        ttl_seconds: int | None = None,
    ) -> None:
        """Set a value in cache with optional TTL."""
        client = get_redis()
        await client.set(self._key(key), value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        """Delete a key from cache."""
        client = get_redis()
        await client.delete(self._key(key))

    async def get_json(self, key: str) -> dict[str, Any] | None:
        """Get and deserialize JSON from cache."""
        import json

        value = await self.get(key)
        if value:
            return json.loads(value)
        return None

    async def set_json(
        self,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        """Serialize and set JSON in cache."""
        import json

        await self.set(key, json.dumps(value), ttl_seconds)

    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        client = get_redis()
        return await client.incrby(self._key(key), amount)
