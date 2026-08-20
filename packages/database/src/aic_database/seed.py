"""Idempotent seed data for tables that are static config, not agent- or
user-written (design doc §5: `ServiceDependency` is "seeded, not
agent-written"). Safe to call repeatedly — upserts, never duplicates.
"""

from __future__ import annotations

from aic_domain.correlation import DEFAULT_SERVICE_DEPENDENCIES
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from aic_database.models import ServiceDependency


def seed_service_dependencies(session: Session) -> None:
    for service, depends_on in DEFAULT_SERVICE_DEPENDENCIES:
        stmt = pg_insert(ServiceDependency).values(service=service, depends_on=depends_on)
        stmt = stmt.on_conflict_do_nothing(index_elements=["service", "depends_on"])
        session.execute(stmt)
