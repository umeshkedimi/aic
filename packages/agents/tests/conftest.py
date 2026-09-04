from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.qdrant import QdrantContainer

DATABASE_DIR = Path(__file__).resolve().parent.parent.parent / "database"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A real, ephemeral, fully-migrated Postgres — the adapter's ledger
    write path is exactly what these tests verify, so a mocked session
    would prove nothing about it."""
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        config = Config(str(DATABASE_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(DATABASE_DIR / "alembic"))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        yield url


@pytest.fixture
def session_factory(postgres_url: str) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(postgres_url)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    """A real, ephemeral Qdrant (T12) — shared across every test module in
    this package that needs one, one container for the whole test session
    rather than one per module."""
    with QdrantContainer() as container:
        yield f"http://{container.rest_host_address}"
