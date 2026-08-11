"""
backend/api/db/session.py
=========================
Async SQLAlchemy engine + session factory.

Provides:
  - `async_engine`     — shared engine (connection pool)
  - `AsyncSessionLocal` — session factory
  - `get_db()`         — FastAPI dependency that yields a scoped session

Connection pool is tuned for production load (100k users, high concurrency).
"""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

DATABASE_URL: str = os.environ["DATABASE_URL"]
# e.g. postgresql+asyncpg://user:pass@localhost:5432/autofill_db

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
async_engine = create_async_engine(
    DATABASE_URL,
    # Pool settings for 100k-user scale
    poolclass=AsyncAdaptedQueuePool,
    pool_size=20,           # baseline connections per worker process
    max_overflow=40,        # burst capacity on top of pool_size
    pool_timeout=30,        # seconds to wait for a connection before raising
    pool_recycle=1800,      # recycle connections every 30 min (avoids stale TCP)
    pool_pre_ping=True,     # send a ping before reusing a connection
    # Echo SQL in development only
    echo=os.environ.get("APP_ENV", "production") == "development",
    echo_pool=False,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # prevent implicit lazy loads after commit
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async database session for use in FastAPI route handlers.

    Usage:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()