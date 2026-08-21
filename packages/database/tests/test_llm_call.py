"""Exercises the `LLMCall` ledger (T5, ADR 0004) against a real Postgres:
the check constraints are actually enforced by the database, not just
declared."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from aic_common.config import Environment
from aic_database.base import Base
from aic_database.models import Incident, LLMCall
from aic_domain.enums import LLMCallStatus
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


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


def test_llm_call_incident_id_is_nullable(session: Session) -> None:
    """Not every LLM call is tied to an incident (e.g. T5's own contract
    tests) — the ledger must accept a standalone call."""
    session.add(
        LLMCall(
            agent_role="contract-test",
            tier="aic-cheap",
            prompt_hash="deadbeef",
            status=LLMCallStatus.OK,
        )
    )
    session.commit()

    row = session.execute(select(LLMCall)).scalar_one()
    assert row.incident_id is None


def test_llm_call_records_two_attempts_for_a_retry(session: Session) -> None:
    incident = Incident(
        fingerprint="llm-call-test",
        service="payment-service",
        environment=Environment.PROD,
        created_at=NOW,
    )
    session.add(incident)
    session.flush()

    session.add(
        LLMCall(
            incident_id=incident.id,
            agent_role="digest",
            tier="aic-cheap",
            prompt_hash="deadbeef",
            attempt=1,
            status=LLMCallStatus.VALIDATION_FAILED,
            error="missing required field 'confidence'",
        )
    )
    session.add(
        LLMCall(
            incident_id=incident.id,
            agent_role="digest",
            tier="aic-cheap",
            model="claude-haiku-4-5-20251001",
            prompt_hash="deadbeef",
            attempt=2,
            input_tokens=120,
            output_tokens=40,
            cost_usd=0.0021,
            latency_ms=850,
            status=LLMCallStatus.OK,
        )
    )
    session.commit()

    rows = (
        session.execute(
            select(LLMCall).where(LLMCall.incident_id == incident.id).order_by(LLMCall.attempt)
        )
        .scalars()
        .all()
    )
    assert [r.status for r in rows] == [LLMCallStatus.VALIDATION_FAILED, LLMCallStatus.OK]


def test_llm_call_attempt_must_be_at_least_one(session: Session) -> None:
    session.add(
        LLMCall(
            agent_role="digest",
            tier="aic-cheap",
            prompt_hash="deadbeef",
            attempt=0,
            status=LLMCallStatus.ERROR,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_llm_call_cost_usd_must_be_non_negative(session: Session) -> None:
    session.add(
        LLMCall(
            agent_role="digest",
            tier="aic-cheap",
            prompt_hash="deadbeef",
            status=LLMCallStatus.OK,
            cost_usd=-0.01,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
