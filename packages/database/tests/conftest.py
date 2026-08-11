from collections.abc import Iterator

import pytest
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A real, ephemeral Postgres — started fresh for this test session.

    Requires a reachable Docker daemon. There is no mocked fallback: the
    thing T1 must prove is that the real Alembic migration applies to a
    real Postgres, which a mock cannot demonstrate.
    """
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        yield container.get_connection_url()
