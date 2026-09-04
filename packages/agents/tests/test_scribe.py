"""Learning stage (design doc §1.13, T12): drafting is tested against a
real resolved-incident shape (real Postgres, real audit-event log) with
only the LLM call faked — same "fake the one seam that talks to a real
model" precedent as T6/T7/T10/T11's own suites — and a real, ephemeral
Qdrant so indexing (and the `POST_REVIEW -> CLOSED` transition it gates)
is proven for real, not assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from aic_agents.knowledge_store import QdrantSettings, search
from aic_agents.port import ModelTier
from aic_agents.scribe import _ScribeOutput, draft_postmortem
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_common.errors import NotFoundError
from aic_database.models import (
    RCA,
    Action,
    ExecutionRecord,
    Incident,
    IncidentEvent,
    Postmortem,
    RemediationProposal,
)
from aic_domain.enums import ActionStatus, ActorType, ExecutionStatus, IncidentStatus, Severity
from aic_domain.state_machine import IllegalTransition
from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"tier": tier, "agent_role": agent_role, "incident_id": incident_id})
        assert response_model is _ScribeOutput
        return response_model(
            timeline="Deploy v42 reduced DB_POOL_SIZE; pool exhausted under load.",
            root_cause_summary="A misconfigured deploy exhausted the DB connection pool.",
            action_taken="Rolled back payment-service to v41.",
            outcome="Latency and error rate returned to baseline.",
            failure_mode="db_connection_pool_exhaustion",
        )


def _seed_resolved_incident(session: Session, *, service: str = "payment-service") -> UUID:
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        title="payment-service checkout failures",
        summary="DB pool exhaustion after a bad deploy",
        severity=Severity.SEV2,
        status=IncidentStatus.RESOLVED,
        service=service,
        environment=Environment.PROD,
        created_at=T0,
        resolved_at=T0,
    )
    session.add(incident)
    session.flush()
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            seq=1,
            event_type="workflow_started",
            actor_type=ActorType.SYSTEM,
            payload={},
            created_at=T0,
        )
    )
    rca = RCA(incident_id=incident.id, agent_version="test", status="draft", created_at=T0)
    session.add(rca)
    session.flush()
    proposal = RemediationProposal(
        incident_id=incident.id, rca_id=rca.id, rationale="rollback", created_at=T0
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
        created_at=T0,
    )
    session.add(action)
    session.flush()
    session.add(
        ExecutionRecord(
            action_id=action.id,
            started_at=T0,
            finished_at=T0,
            status=ExecutionStatus.SUCCEEDED.value,
            output={},
        )
    )
    session.commit()
    return incident.id


@pytest.fixture
def qdrant_settings(qdrant_url: str) -> QdrantSettings:
    return QdrantSettings(base_url=qdrant_url, collection=f"pm-scribe-test-{uuid4().hex}")


@pytest.fixture
def qdrant_client(qdrant_url: str) -> AsyncQdrantClient:
    return AsyncQdrantClient(url=qdrant_url)


async def test_draft_postmortem_persists_and_indexes_and_closes_incident(
    session_factory: sessionmaker[Session],
    qdrant_client: AsyncQdrantClient,
    qdrant_settings: QdrantSettings,
) -> None:
    llm = _FakeLLM()
    with session_factory() as session:
        incident_id = _seed_resolved_incident(session)

    with session_factory() as session:
        postmortem = await draft_postmortem(
            session,
            incident_id,
            llm=llm,
            clock=FixedClock(T0),
            qdrant_client=qdrant_client,
            qdrant_settings=qdrant_settings,
        )
        session.commit()

    assert postmortem.incident_id == incident_id
    assert len(postmortem.embedding_refs) >= 1
    assert "db_connection_pool_exhaustion" in postmortem.content

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.CLOSED

        stored = session.execute(
            select(Postmortem).where(Postmortem.incident_id == incident_id)
        ).scalar_one()
        assert stored.id == postmortem.id

        events = list(
            session.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.seq)
            )
            .scalars()
            .all()
        )
        assert events[-1].event_type == "post_review"
        assert events[-1].actor_type == ActorType.LLM

    assert len(llm.calls) == 1
    assert llm.calls[0]["agent_role"] == "scribe"
    assert llm.calls[0]["tier"] == ModelTier.CHEAP

    hits = await search(
        client=qdrant_client,
        settings=qdrant_settings,
        query="database connection pool exhaustion",
        limit=3,
    )
    assert any(hit.incident_id == incident_id for hit in hits)
    assert any(hit.resolution_action_type == "RollbackDeployment" for hit in hits)


async def test_draft_postmortem_rejects_an_incident_that_is_not_resolved(
    session_factory: sessionmaker[Session],
    qdrant_client: AsyncQdrantClient,
    qdrant_settings: QdrantSettings,
) -> None:
    llm = _FakeLLM()
    with session_factory() as session:
        incident_id = _seed_resolved_incident(session)
        incident = session.get(Incident, incident_id)
        assert incident is not None
        incident.status = IncidentStatus.INVESTIGATING
        session.commit()

    with (
        session_factory() as session,
        pytest.raises(IllegalTransition),
    ):
        await draft_postmortem(
            session,
            incident_id,
            llm=llm,
            clock=FixedClock(T0),
            qdrant_client=qdrant_client,
            qdrant_settings=qdrant_settings,
        )

    # Fails fast, before any LLM spend.
    assert llm.calls == []


async def test_draft_postmortem_raises_not_found_for_unknown_incident(
    session_factory: sessionmaker[Session],
    qdrant_client: AsyncQdrantClient,
    qdrant_settings: QdrantSettings,
) -> None:
    with session_factory() as session, pytest.raises(NotFoundError):
        await draft_postmortem(
            session,
            uuid4(),
            llm=_FakeLLM(),
            clock=FixedClock(T0),
            qdrant_client=qdrant_client,
            qdrant_settings=qdrant_settings,
        )
