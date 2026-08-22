from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from aic_agents.port import ModelTier
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import Incident, IncidentEvent, IncidentSignal
from aic_domain.enums import IncidentStatus
from aic_triage.main import _find_next_triaging_incident_id, _poll_once
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(autouse=True)
def _clean_incident_tables(session_factory: sessionmaker[Session]) -> None:
    """`_find_next_triaging_incident_id` scans the *whole* incident table by
    design (it's a global poller, not scoped to one test's data) — unlike
    `aic_correlator`'s tests, which isolate via a unique fingerprint/group
    per test, these tests need an actually-clean table per test since the
    thing under test is "the oldest TRIAGING incident in the whole table"."""
    with session_factory() as session:
        session.execute(delete(IncidentEvent))
        session.execute(delete(IncidentSignal))
        session.execute(delete(Incident))
        session.commit()


class _FakeLLM:
    async def complete_structured[T: BaseModel](
        self,
        *,
        tier: ModelTier,
        agent_role: str,
        system: str,
        user: str,
        response_model: type[T],
        incident_id: UUID | None = None,
    ) -> T:
        return response_model.model_validate({"title": "Fake title", "summary": "Fake summary"})


def _make_incident(
    session: Session, *, status: IncidentStatus, created_at: datetime, with_signal: bool = True
) -> UUID:
    incident = Incident(
        fingerprint=f"checkout-service:{uuid4()}",
        service="checkout-service",
        environment=Environment.PROD,
        status=status,
        created_at=created_at,
    )
    session.add(incident)
    session.flush()
    if with_signal:
        session.add(
            IncidentSignal(
                incident_id=incident.id,
                alert_fingerprint=f"fp-{uuid4()}",
                alertname="HighLatencyPaymentService",
                service="payment-service",
                labels={},
                starts_at=created_at,
            )
        )
    session.commit()
    incident_id: UUID = incident.id
    return incident_id


def test_find_next_triaging_incident_id_returns_none_when_nothing_to_triage(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _make_incident(session, status=IncidentStatus.INVESTIGATING, created_at=datetime.now(UTC))

    assert _find_next_triaging_incident_id(session_factory) is None


def test_find_next_triaging_incident_id_returns_oldest_first(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        newer_id = _make_incident(session, status=IncidentStatus.TRIAGING, created_at=now)
        older_id = _make_incident(
            session, status=IncidentStatus.TRIAGING, created_at=now - timedelta(minutes=5)
        )

    assert _find_next_triaging_incident_id(session_factory) == older_id
    assert _find_next_triaging_incident_id(session_factory) != newer_id


async def test_poll_once_triages_and_advances_the_incident(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id = _make_incident(
            session, status=IncidentStatus.TRIAGING, created_at=datetime.now(UTC)
        )

    processed = await _poll_once(session_factory, _FakeLLM(), FixedClock(datetime.now(UTC)))
    assert processed is True

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.INVESTIGATING
        assert incident.title == "Fake title"
        assert incident.severity is not None


async def test_poll_once_returns_false_when_nothing_to_triage(
    session_factory: sessionmaker[Session],
) -> None:
    processed = await _poll_once(session_factory, _FakeLLM(), FixedClock(datetime.now(UTC)))
    assert processed is False
