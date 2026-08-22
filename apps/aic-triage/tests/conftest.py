from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

DATABASE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "packages" / "database"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A real, ephemeral, fully-migrated Postgres — this worker's whole job
    is reading/writing real Incident rows, so a mocked session would prove
    nothing about the polling behavior this task is about."""
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
