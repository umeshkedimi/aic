"""Pytest configuration and fixtures.

Provides shared fixtures for database, API client, and test data.
"""

import asyncio
from typing import AsyncGenerator, Generator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from aic.api.app import create_app
from aic.config import Settings
from aic.infrastructure.database.models import Base


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Test settings with in-memory database."""
    return Settings(
        env="test",
        debug=True,
        database_url="postgresql+asyncpg://aic:aic_dev_password@localhost:5432/aic_test",
        redis_url="redis://localhost:6379/1",
        otlp_enabled=False,
        metrics_enabled=False,
    )


@pytest_asyncio.fixture(scope="session")
async def test_engine(test_settings: Settings):
    """Create test database engine."""
    engine = create_async_engine(
        str(test_settings.database_url),
        poolclass=NullPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Get test database session with rollback."""
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_settings: Settings) -> AsyncGenerator[AsyncClient, None]:
    """Get async HTTP client for API testing."""
    app = create_app(test_settings)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def sample_incident_data() -> dict:
    """Sample incident data for testing."""
    return {
        "title": "High CPU usage on payment-service",
        "description": "Payment service pods showing >90% CPU utilization",
        "severity": "high",
        "source": "alertmanager",
        "service": "payment-service",
        "environment": "production",
        "tags": ["infrastructure", "performance"],
        "metadata": {"alert_id": "alert-123"},
    }


@pytest.fixture
def sample_incident_with_external_id(sample_incident_data: dict) -> dict:
    """Sample incident with external ID."""
    return {
        **sample_incident_data,
        "external_id": f"ext-{uuid4().hex[:8]}",
    }
