"""`seed_service_dependencies` must be safe to rerun (T4: aic-correlator
reads this table at startup, and re-running `make demo-seed` against an
already-seeded cluster must not error or duplicate rows).

Uses `Base.metadata.create_all`/`drop_all` directly rather than an Alembic
upgrade, matching `test_orm_models.py`'s pattern — the migration itself is
already proven separately in `test_migrations.py`, and doing it this way
keeps this test's schema setup self-contained rather than depending on
Alembic's `alembic_version` bookkeeping staying in sync with whatever else
ran earlier against the shared session-scoped `postgres_url` container.
"""

from collections.abc import Iterator

import pytest
from aic_database.base import Base
from aic_database.models import ServiceDependency
from aic_database.seed import seed_service_dependencies
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def session(postgres_url: str) -> Iterator[Session]:
    engine = create_engine(postgres_url)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_seeding_twice_does_not_duplicate_rows(session: Session) -> None:
    seed_service_dependencies(session)
    session.commit()
    seed_service_dependencies(session)
    session.commit()

    rows = session.execute(select(ServiceDependency)).scalars().all()

    assert [(row.service, row.depends_on) for row in rows] == [
        ("checkout-service", "payment-service")
    ]
