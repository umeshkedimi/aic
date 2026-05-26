"""Async database session management.

Provides connection pooling, session lifecycle management,
and health checking for PostgreSQL via asyncpg.
"""

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
    AsyncEngine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy import text

from aic.config import Settings
from aic.observability.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    healthy: bool
    latency_ms: float | None = None
    message: str | None = None


async def init_db(settings: Settings) -> None:
    """Initialize database connection pool.

    Creates the async engine and session factory with appropriate
    pool settings for the environment.
    """
    global _engine, _session_factory

    logger.info("Initializing database connection", url=str(settings.database_url).split("@")[-1])

    # Use NullPool for testing, regular pool for production
    pool_class = NullPool if settings.env == "test" else None

    _engine = create_async_engine(
        str(settings.database_url),
        echo=settings.database_echo,
        pool_size=settings.database_pool_size if pool_class is None else 0,
        max_overflow=settings.database_max_overflow if pool_class is None else 0,
        pool_timeout=settings.database_pool_timeout,
        poolclass=pool_class,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Verify connection
    async with _engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    logger.info("Database connection initialized")


async def close_db() -> None:
    """Close database connection pool."""
    global _engine, _session_factory

    if _engine:
        logger.info("Closing database connection")
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection closed")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session.

    Usage:
        async with get_session() as session:
            result = await session.execute(query)

    The session is automatically committed on successful exit
    and rolled back on exception.
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_db_health() -> HealthCheckResult:
    """Check database health with a simple query.

    Returns health status and query latency.
    """
    if _engine is None:
        return HealthCheckResult(
            healthy=False,
            message="Database not initialized",
        )

    try:
        start = time.perf_counter()
        async with _engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000

        return HealthCheckResult(
            healthy=True,
            latency_ms=round(latency_ms, 2),
        )
    except Exception as e:
        logger.warning("Database health check failed", error=str(e))
        return HealthCheckResult(
            healthy=False,
            message=str(e),
        )


def get_engine() -> AsyncEngine:
    """Get the database engine (for advanced use cases like migrations)."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine
