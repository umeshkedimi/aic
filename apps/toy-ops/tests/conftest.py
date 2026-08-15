from collections.abc import Iterator

import pytest
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A real, ephemeral Postgres for the `record_deployment` integration
    test — the deploy script's whole point is writing a real row, so a
    mocked session would prove nothing."""
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        yield container.get_connection_url()
