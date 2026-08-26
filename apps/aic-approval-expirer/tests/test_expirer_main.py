from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from aic_approval_expirer.main import _find_next_due_request_id, _poll_once
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import (
    RCA,
    Action,
    ApprovalRequest,
    Incident,
    IncidentEvent,
    RemediationProposal,
)
from aic_domain.enums import ApprovalRequestStatus, IncidentStatus
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    """`_find_next_due_request_id` scans the whole `approval_request` table
    by design (a global poller), same reason T6/T7/T8's poller tests need
    this."""
    with session_factory() as session:
        session.execute(delete(ApprovalRequest))
        session.execute(delete(Action))
        session.execute(delete(RemediationProposal))
        session.execute(delete(RCA))
        session.execute(delete(IncidentEvent))
        session.execute(delete(Incident))
        session.commit()


def _make_pending_request(
    session: Session,
    *,
    expires_at: datetime,
    status: str = ApprovalRequestStatus.PENDING.value,
    incident_status: IncidentStatus = IncidentStatus.AWAITING_APPROVAL,
) -> tuple[UUID, UUID]:
    now = datetime.now(UTC)
    service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        service=service,
        environment=Environment.PROD,
        status=incident_status,
        created_at=now,
    )
    session.add(incident)
    session.flush()
    rca = RCA(incident_id=incident.id, agent_version="test", status="draft", created_at=now)
    session.add(rca)
    session.flush()
    proposal = RemediationProposal(
        incident_id=incident.id, rca_id=rca.id, rationale="r", created_at=now
    )
    session.add(proposal)
    session.flush()
    action = Action(
        proposal_id=proposal.id,
        action_type="RollbackDeployment",
        params={},
        target_resource=service,
        status="pending_approval",
        idempotency_key=f"{incident.id}:{rca.id}:RollbackDeployment",
        created_at=now,
    )
    session.add(action)
    session.flush()
    request = ApprovalRequest(
        action_id=action.id,
        quorum=1,
        required_roles=["sre"],
        expires_at=expires_at,
        status=status,
        created_at=now,
    )
    session.add(request)
    session.commit()
    return incident.id, request.id


def test_find_next_due_request_id_returns_none_when_nothing_due(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        _make_pending_request(session, expires_at=now + timedelta(minutes=30))

    assert _find_next_due_request_id(session_factory, clock=FixedClock(now)) is None


def test_find_next_due_request_id_skips_non_pending_requests(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        _make_pending_request(
            session,
            expires_at=now - timedelta(minutes=1),
            status=ApprovalRequestStatus.APPROVED.value,
            incident_status=IncidentStatus.REMEDIATING,
        )

    assert _find_next_due_request_id(session_factory, clock=FixedClock(now)) is None


def test_find_next_due_request_id_returns_earliest_due_first(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        _make_pending_request(session, expires_at=now - timedelta(minutes=1))
        _earliest_incident_id, earliest_request_id = _make_pending_request(
            session, expires_at=now - timedelta(minutes=10)
        )

    result = _find_next_due_request_id(session_factory, clock=FixedClock(now))
    assert result == earliest_request_id


async def test_poll_once_expires_and_escalates(session_factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        incident_id, request_id = _make_pending_request(
            session, expires_at=now - timedelta(seconds=1)
        )

    processed = await _poll_once(session_factory, FixedClock(now))
    assert processed is True

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.EXPIRED.value
        assert incident.status == IncidentStatus.ESCALATED

    # A second poll must not re-pick the same request: status is no longer pending.
    assert _find_next_due_request_id(session_factory, clock=FixedClock(now)) is None


async def test_poll_once_returns_false_when_nothing_due(
    session_factory: sessionmaker[Session],
) -> None:
    processed = await _poll_once(session_factory, FixedClock(datetime.now(UTC)))
    assert processed is False
