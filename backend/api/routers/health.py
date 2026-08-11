"""
backend/api/routers/health.py
==============================
Liveness and readiness endpoints used by Docker / Kubernetes health checks
and the load balancer.

GET /health/live    → Always 200 if the process is running (liveness)
GET /health/ready   → 200 only if Postgres + Redis + Qdrant are reachable (readiness)
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import get_qdrant
from backend.api.db.redis_client import get_redis
from backend.api.db.session import get_db

logger = get_logger(__name__)
router = APIRouter()


@router.get("/live", include_in_schema=False)
async def liveness() -> JSONResponse:
    """Kubernetes liveness probe — always 200 if the process is up."""
    return JSONResponse({"status": "alive"})


@router.get("/ready", tags=["Health"])
async def readiness(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    qdrant: AsyncQdrantClient = Depends(get_qdrant),
) -> JSONResponse:
    """
    Readiness probe — checks all three data stores.
    Returns 503 if any dependency is unreachable so the load balancer
    can stop routing traffic to an unhealthy instance.
    """
    checks: dict[str, bool] = {}
    start = time.monotonic()

    # PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception as exc:
        logger.error("Postgres health check failed", error=str(exc))
        checks["postgres"] = False

    # Redis
    try:
        await redis.ping()
        checks["redis"] = True
    except Exception as exc:
        logger.error("Redis health check failed", error=str(exc))
        checks["redis"] = False

    # Qdrant
    try:
        await qdrant.get_collections()
        checks["qdrant"] = True
    except Exception as exc:
        logger.error("Qdrant health check failed", error=str(exc))
        checks["qdrant"] = False

    all_healthy = all(checks.values())
    latency_ms = round((time.monotonic() - start) * 1000, 2)

    return JSONResponse(
        status_code=status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if all_healthy else "degraded",
            "checks": checks,
            "latency_ms": latency_ms,
        },
    )