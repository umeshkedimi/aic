from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from aic_agents.execution import ServiceAccountCredentials
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import (
    RCA,
    Action,
    ExecutionRecord,
    Incident,
    IncidentEvent,
    RemediationProposal,
)
from aic_domain.enums import ActionStatus, IncidentStatus
from aic_executor.main import _find_next_approved_action_id, _poll_once
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

FAKE_CREDENTIALS = ServiceAccountCredentials(
    server="https://fake-k8s.invalid:6443",
    ca_cert_path=Path("/dev/null"),
    token="fake-executor-token",
    namespace="aic-demo",
)


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    """`_find_next_approved_action_id` scans the *whole* action/incident
    tables by design (a global poller, not scoped to one test's data) —
    same reason T6/T7/T8's poller tests need this."""
    with session_factory() as session:
        session.execute(delete(ExecutionRecord))
        session.execute(delete(Action))
        session.execute(delete(RemediationProposal))
        session.execute(delete(RCA))
        session.execute(delete(IncidentEvent))
        session.execute(delete(Incident))
        session.commit()


class _FakeRunner:
    def __init__(self, *, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return subprocess.CompletedProcess(args, returncode=self.returncode, stdout="", stderr="")


def _make_action(
    session: Session,
    *,
    incident_status: IncidentStatus,
    action_status: str,
    created_at: datetime,
) -> tuple[UUID, UUID]:
    service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        service=service,
        environment=Environment.PROD,
        status=incident_status,
        created_at=created_at,
    )
    session.add(incident)
    session.flush()

    rca = RCA(incident_id=incident.id, agent_version="test", status="draft", created_at=created_at)
    session.add(rca)
    session.flush()

    proposal = RemediationProposal(
        incident_id=incident.id, rca_id=rca.id, rationale="r", created_at=created_at
    )
    session.add(proposal)
    session.flush()

    action = Action(
        proposal_id=proposal.id,
        action_type="RollbackDeployment",
        params={"deployment": service, "from_version": "v42", "to_version": "v41"},
        target_resource=service,
        status=action_status,
        idempotency_key=f"{incident.id}:{rca.id}:RollbackDeployment",
        created_at=created_at,
    )
    session.add(action)
    session.commit()
    return incident.id, action.id


def test_find_next_approved_action_id_returns_none_when_nothing_to_execute(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _make_action(
            session,
            incident_status=IncidentStatus.AWAITING_APPROVAL,
            action_status=ActionStatus.PENDING_APPROVAL.value,
            created_at=datetime.now(UTC),
        )

    assert _find_next_approved_action_id(session_factory) is None


def test_find_next_approved_action_id_skips_incident_not_remediating(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        # Approved action, but its incident somehow isn't REMEDIATING —
        # should never be picked.
        _make_action(
            session,
            incident_status=IncidentStatus.AWAITING_APPROVAL,
            action_status=ActionStatus.APPROVED.value,
            created_at=datetime.now(UTC),
        )

    assert _find_next_approved_action_id(session_factory) is None


def test_find_next_approved_action_id_returns_oldest_first(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        _newer_incident, newer_action = _make_action(
            session,
            incident_status=IncidentStatus.REMEDIATING,
            action_status=ActionStatus.APPROVED.value,
            created_at=now,
        )
        _older_incident, older_action = _make_action(
            session,
            incident_status=IncidentStatus.REMEDIATING,
            action_status=ActionStatus.APPROVED.value,
            created_at=now - timedelta(minutes=5),
        )

    assert _find_next_approved_action_id(session_factory) == older_action
    assert _find_next_approved_action_id(session_factory) != newer_action


async def test_poll_once_executes_the_action_and_advances_the_incident(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, action_id = _make_action(
            session,
            incident_status=IncidentStatus.REMEDIATING,
            action_status=ActionStatus.APPROVED.value,
            created_at=datetime.now(UTC),
        )

    fake_runner = _FakeRunner(returncode=0)
    processed = await _poll_once(
        session_factory,
        FAKE_CREDENTIALS,
        FixedClock(datetime.now(UTC)),
        "kubectl",
        fake_runner,
    )
    assert processed is True
    assert len(fake_runner.calls) == 1

    with session_factory() as session:
        action = session.get(Action, action_id)
        incident = session.get(Incident, incident_id)
        assert action is not None
        assert incident is not None
        assert action.status == ActionStatus.EXECUTED.value
        assert incident.status == IncidentStatus.VERIFYING

    # A second poll must not re-pick the same action: its status is no
    # longer `approved`, and no second kubectl call is made.
    processed_again = await _poll_once(
        session_factory, FAKE_CREDENTIALS, FixedClock(datetime.now(UTC)), "kubectl", fake_runner
    )
    assert processed_again is False
    assert len(fake_runner.calls) == 1
