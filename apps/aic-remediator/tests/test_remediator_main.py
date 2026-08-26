from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from aic_agents.port import ModelTier
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import (
    RCA,
    Action,
    ApprovalDecision,
    ApprovalRequest,
    Deployment,
    Evidence,
    Hypothesis,
    Incident,
    IncidentEvent,
    PolicyDecision,
    RemediationProposal,
)
from aic_domain.enums import ActionType, EvidenceStatus, IncidentStatus
from aic_remediator.main import _find_next_investigated_incident_id, _poll_once
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    """`_find_next_investigated_incident_id` scans the *whole* incident
    table by design (a global poller, not scoped to one test's data) —
    same reason `aic_triage`'s tests need this (T6's note)."""
    with session_factory() as session:
        session.execute(delete(ApprovalDecision))
        session.execute(delete(ApprovalRequest))
        session.execute(delete(PolicyDecision))
        session.execute(delete(Action))
        session.execute(delete(RemediationProposal))
        session.execute(delete(Hypothesis))
        session.execute(delete(RCA))
        session.execute(delete(Evidence))
        session.execute(delete(IncidentEvent))
        session.execute(delete(Incident))
        session.execute(delete(Deployment))
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
        return response_model.model_validate(
            {"chosen_action_type": ActionType.ROLLBACK_DEPLOYMENT.value, "rationale": "fake"}
        )


def _make_incident(
    session: Session, *, status: IncidentStatus, created_at: datetime, with_rca: bool = True
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

    if with_rca:
        service = f"payment-service-{uuid4().hex[:8]}"
        session.add(
            Deployment(
                service=service,
                version="v41",
                image_tag="v41",
                config_diff={},
                deployed_at=created_at - timedelta(hours=2),
                deployed_by="test",
            )
        )
        session.add(
            Deployment(
                service=service,
                version="v42",
                image_tag="v42",
                config_diff={"DB_POOL_SIZE": {"from": 20, "to": 3}},
                deployed_at=created_at - timedelta(hours=1),
                deployed_by="test",
            )
        )
        session.flush()

        evidence = Evidence(
            incident_id=incident.id,
            source="postgres",
            tool="k8s.get_deployment_history",
            query=f"service={service}",
            result_digest="[]",
            collected_at=created_at,
            status=EvidenceStatus.OK,
        )
        session.add(evidence)
        session.flush()

        rca = RCA(
            incident_id=incident.id,
            agent_version="test",
            status="draft",
            created_at=created_at,
        )
        session.add(rca)
        session.flush()

        session.add(
            Hypothesis(
                rca_id=rca.id,
                rank=1,
                statement="The bad deploy exhausted the DB pool",
                confidence=0.9,
                evidence_ids=[str(evidence.id)],
                counter_evidence=[],
            )
        )

    session.commit()
    incident_id: UUID = incident.id
    return incident_id


def test_find_next_investigated_incident_id_returns_none_when_nothing_to_remediate(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _make_incident(
            session,
            status=IncidentStatus.INVESTIGATING,
            created_at=datetime.now(UTC),
            with_rca=False,
        )

    assert _find_next_investigated_incident_id(session_factory) is None


def test_find_next_investigated_incident_id_skips_incident_without_rca_yet(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        _make_incident(session, status=IncidentStatus.INVESTIGATING, created_at=now, with_rca=False)
        with_rca_id = _make_incident(
            session,
            status=IncidentStatus.INVESTIGATING,
            created_at=now - timedelta(minutes=5),
            with_rca=True,
        )

    assert _find_next_investigated_incident_id(session_factory) == with_rca_id


def test_find_next_investigated_incident_id_returns_oldest_first(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        newer_id = _make_incident(session, status=IncidentStatus.INVESTIGATING, created_at=now)
        older_id = _make_incident(
            session, status=IncidentStatus.INVESTIGATING, created_at=now - timedelta(minutes=5)
        )

    assert _find_next_investigated_incident_id(session_factory) == older_id
    assert _find_next_investigated_incident_id(session_factory) != newer_id


async def test_poll_once_plans_remediation_and_advances_the_incident(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id = _make_incident(
            session, status=IncidentStatus.INVESTIGATING, created_at=datetime.now(UTC)
        )

    processed = await _poll_once(session_factory, _FakeLLM(), FixedClock(datetime.now(UTC)))
    assert processed is True

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.AWAITING_APPROVAL

    # A second poll must not re-pick the same incident: `status` already
    # moved away from `INVESTIGATING`.
    assert _find_next_investigated_incident_id(session_factory) is None


async def test_poll_once_returns_false_when_nothing_to_remediate(
    session_factory: sessionmaker[Session],
) -> None:
    processed = await _poll_once(session_factory, _FakeLLM(), FixedClock(datetime.now(UTC)))
    assert processed is False
