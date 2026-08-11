"""Exercises the ORM models against a real Postgres: constraints (FK,
unique, check) actually enforced by the database, not just declared."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from aic_common.config import Environment
from aic_database.base import Base
from aic_database.models import Evidence, Incident, IncidentEvent
from aic_domain.enums import ActorType, EvidenceStatus, IncidentStatus
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


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


def test_incident_fingerprint_is_unique(session: Session) -> None:
    session.add(
        Incident(
            fingerprint="dup",
            service="payment-service",
            environment=Environment.PROD,
            created_at=NOW,
        )
    )
    session.commit()

    session.add(
        Incident(
            fingerprint="dup",
            service="checkout-service",
            environment=Environment.PROD,
            created_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_incident_event_seq_is_unique_per_incident(session: Session) -> None:
    incident = Incident(
        fingerprint="incident-events-test",
        service="payment-service",
        environment=Environment.PROD,
        created_at=NOW,
    )
    session.add(incident)
    session.flush()

    session.add(
        IncidentEvent(
            incident_id=incident.id,
            seq=1,
            event_type="workflow_started",
            actor_type=ActorType.SYSTEM,
        )
    )
    session.commit()

    session.add(
        IncidentEvent(
            incident_id=incident.id,
            seq=1,
            event_type="duplicate_seq",
            actor_type=ActorType.SYSTEM,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_evidence_requires_a_real_incident(session: Session) -> None:
    session.add(
        Evidence(
            incident_id=uuid.uuid4(),
            source="prometheus",
            tool="range_query",
            status=EvidenceStatus.OK,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_evidence_latency_check_constraint(session: Session) -> None:
    incident = Incident(
        fingerprint="evidence-latency-test",
        service="payment-service",
        environment=Environment.PROD,
        created_at=NOW,
    )
    session.add(incident)
    session.flush()

    session.add(
        Evidence(
            incident_id=incident.id,
            source="prometheus",
            tool="range_query",
            status=EvidenceStatus.OK,
            latency_ms=-1,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_incident_status_defaults_to_open(session: Session) -> None:
    incident = Incident(
        fingerprint="default-status-test",
        service="payment-service",
        environment=Environment.PROD,
        created_at=NOW,
    )
    session.add(incident)
    session.commit()
    session.refresh(incident)
    assert incident.status == IncidentStatus.OPEN
