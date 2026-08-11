"""
backend/api/db/redis_client.py
==============================
Async Redis connection pool using redis-py (v5+) with asyncio support.

Provides:
  - `init_redis_pool()`    — called at app startup
  - `close_redis_pool()`   — called at app shutdown
  - `get_redis()`          — FastAPI dependency that yields the pool
  - `RedisCache`           — thin helper class for type-safe cache operations

The same Redis instance is used for:
  - Response caching (user profiles, template lists)
  - Embedding vector caching (avoids re-embedding identical text)
  - Clerk JWKS caching
  - Celery broker (on a separate DB index — see config.py)
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool

from backend.api.core.config import settings
from backend.api.core.logging import get_logger

logger = get_logger(__name__)

# Module-level pool — initialised once in lifespan, shared across requests
_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None

T = TypeVar("T")


async def init_redis_pool() -> None:
    """
    Create the async Redis connection pool.
    Called once from the FastAPI lifespan context manager.
    """
    global _redis_pool, _redis_client

    logger.info("Initialising Redis connection pool", url=settings.REDIS_URL)

    _redis_pool = ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=50,       # per worker process
        socket_timeout=5.0,       # seconds before a blocked command times out
        socket_connect_timeout=5.0,
        retry_on_timeout=True,
        health_check_interval=30, # background ping every 30 s
        decode_responses=True,    # all keys/values returned as str, not bytes
    )
    _redis_client = Redis(connection_pool=_redis_pool)

    # Smoke test
    await _redis_client.ping()
    logger.info("Redis connection pool ready")


async def close_redis_pool() -> None:
    """Gracefully drain the pool on application shutdown."""
    global _redis_pool, _redis_client
    if _redis_client:
        await _redis_client.aclose()
    if _redis_pool:
        await _redis_pool.aclose()
    logger.info("Redis connection pool closed")


async def get_redis() -> Redis:
    """
    FastAPI dependency — yields the shared Redis client.

    Usage:
        @router.get("/example")
        async def example(redis: Redis = Depends(get_redis)):
            value = await redis.get("some-key")
    """
    if _redis_client is None:
        raise RuntimeError(
            "Redis pool has not been initialised. "
            "Ensure init_redis_pool() is called in the app lifespan."
        )
    return _redis_client


# ---------------------------------------------------------------------------
# RedisCache — typed helper on top of the raw client
# ---------------------------------------------------------------------------

class RedisCache:
    """
    Thin wrapper around the Redis client providing:
      - JSON serialisation / deserialisation
      - Configurable TTL defaults
      - Namespace-prefixed keys to prevent collisions

    Usage:
        cache = RedisCache(prefix="user_profile", ttl=300)
        await cache.set(redis, user_id, {"name": "Jane"})
        data = await cache.get(redis, user_id)
    """

    def __init__(self, prefix: str, ttl: int = settings.REDIS_CACHE_TTL_SECONDS) -> None:
        self.prefix = prefix
        self.ttl = ttl

    def _key(self, identifier: str) -> str:
        return f"{self.prefix}:{identifier}"

    async def get(self, redis: Redis, identifier: str) -> Any | None:
        """Return the cached value or None if missing / expired."""
        raw = await redis.get(self._key(identifier))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Cache JSON decode error", key=self._key(identifier))
            return None

    async def set(self, redis: Redis, identifier: str, value: Any, ttl: int | None = None) -> None:
        """Store value as JSON with optional TTL override."""
        await redis.setex(
            self._key(identifier),
            ttl if ttl is not None else self.ttl,
            json.dumps(value, default=str),
        )

    async def delete(self, redis: Redis, identifier: str) -> None:
        """Invalidate a cached entry."""
        await redis.delete(self._key(identifier))

    async def exists(self, redis: Redis, identifier: str) -> bool:
        return bool(await redis.exists(self._key(identifier)))


# ---------------------------------------------------------------------------
# Pre-configured cache namespaces used across the application
# ---------------------------------------------------------------------------

# Cache for user profile data (short TTL — plan tier can change)
user_profile_cache = RedisCache(prefix="user_profile", ttl=120)

# Cache for template lists (medium TTL)
template_list_cache = RedisCache(prefix="template_list", ttl=300)

# Cache for embedding vectors (long TTL — text rarely changes)
embedding_cache = RedisCache(prefix="embedding", ttl=settings.REDIS_EMBEDDING_CACHE_TTL)

# Cache for Clerk JWKS public keys (1 hour)
jwks_cache = RedisCache(prefix="clerk_jwks", ttl=3600)