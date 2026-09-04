from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from aic_agents.knowledge_store import QdrantSettings
from aic_agents.port import ModelTier
from aic_agents.scribe import _ScribeOutput
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import (
    RCA,
    Action,
    Evidence,
    ExecutionRecord,
    Hypothesis,
    Incident,
    IncidentEvent,
    IncidentSignal,
    Postmortem,
    RemediationProposal,
)
from aic_domain.enums import ActionStatus, ExecutionStatus, IncidentStatus, Severity
from aic_scribe.main import _find_next_resolved_incident_id, _poll_once
from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    """`_find_next_resolved_incident_id` scans the whole incident table by
    design (a global poller, not scoped to one test's data) — see the
    identical note in apps/aic-triage/tests/test_main.py (T6) and
    apps/aic-verifier/tests/test_verifier_main.py (T11)."""
    with session_factory() as session:
        session.execute(delete(Postmortem))
        session.execute(delete(ExecutionRecord))
        session.execute(delete(Action))
        session.execute(delete(RemediationProposal))
        session.execute(delete(IncidentEvent))
        session.execute(delete(Evidence))
        session.execute(delete(Hypothesis))
        session.execute(delete(RCA))
        session.execute(delete(IncidentSignal))
        session.execute(delete(Incident))
        session.commit()


@pytest.fixture
def qdrant_settings(qdrant_url: str) -> QdrantSettings:
    return QdrantSettings(base_url=qdrant_url, collection=f"pm-scribe-app-test-{uuid4().hex}")


@pytest.fixture
def qdrant_client(qdrant_url: str) -> AsyncQdrantClient:
    return AsyncQdrantClient(url=qdrant_url)


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
        assert response_model is _ScribeOutput
        return response_model.model_validate(
            {
                "timeline": "Deploy v42 exhausted the DB pool.",
                "root_cause_summary": "Misconfigured deploy.",
                "action_taken": "Rolled back.",
                "outcome": "Resolved.",
                "failure_mode": "db_connection_pool_exhaustion",
            }
        )


def _make_resolved_incident(session: Session, *, created_at: datetime) -> UUID:
    service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        title="checkout failures",
        severity=Severity.SEV2,
        service=service,
        environment=Environment.PROD,
        status=IncidentStatus.RESOLVED,
        created_at=created_at,
        resolved_at=created_at,
    )
    session.add(incident)
    session.flush()
    rca = RCA(incident_id=incident.id, agent_version="test", status="draft", created_at=created_at)
    session.add(rca)
    session.flush()
    proposal = RemediationProposal(
        incident_id=incident.id, rca_id=rca.id, rationale="rollback", created_at=created_at
    )
    session.add(proposal)
    session.flush()
    action = Action(
        proposal_id=proposal.id,
        action_type="RollbackDeployment",
        params={},
        target_resource=service,
        status=ActionStatus.EXECUTED.value,
        idempotency_key=f"{incident.id}:{rca.id}:RollbackDeployment",
        created_at=created_at,
    )
    session.add(action)
    session.flush()
    session.add(
        ExecutionRecord(
            action_id=action.id,
            started_at=created_at,
            finished_at=created_at,
            status=ExecutionStatus.SUCCEEDED.value,
            output={},
        )
    )
    session.commit()
    incident_id: UUID = incident.id
    return incident_id


def test_find_next_resolved_incident_id_returns_none_when_nothing_to_scribe(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident = Incident(
            fingerprint=f"x:{uuid4()}",
            service="payment-service",
            environment=Environment.PROD,
            status=IncidentStatus.INVESTIGATING,
            created_at=T0,
        )
        session.add(incident)
        session.commit()

    assert _find_next_resolved_incident_id(session_factory) is None


def test_find_next_resolved_incident_id_returns_oldest_first(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        newer_id = _make_resolved_incident(session, created_at=T0)
        older_id = _make_resolved_incident(session, created_at=T0 - timedelta(minutes=5))

    assert _find_next_resolved_incident_id(session_factory) == older_id
    assert _find_next_resolved_incident_id(session_factory) != newer_id


async def test_poll_once_drafts_a_postmortem_and_closes_the_incident(
    session_factory: sessionmaker[Session],
    qdrant_client: AsyncQdrantClient,
    qdrant_settings: QdrantSettings,
) -> None:
    with session_factory() as session:
        incident_id = _make_resolved_incident(session, created_at=T0)

    processed = await _poll_once(
        session_factory, _FakeLLM(), FixedClock(T0), qdrant_client, qdrant_settings
    )
    assert processed is True

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.CLOSED

    # The incident is no longer RESOLVED, so a second poll must not re-pick
    # it — same "the operation itself moves status away" guard T8's
    # remediator/T10's executor pollers rely on.
    assert _find_next_resolved_incident_id(session_factory) is None


async def test_poll_once_returns_false_when_nothing_to_scribe(
    session_factory: sessionmaker[Session],
    qdrant_client: AsyncQdrantClient,
    qdrant_settings: QdrantSettings,
) -> None:
    processed = await _poll_once(
        session_factory, _FakeLLM(), FixedClock(T0), qdrant_client, qdrant_settings
    )
    assert processed is False
