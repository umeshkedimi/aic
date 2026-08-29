"""The executor (design doc §1.4 ACT row, §1.11, ADR 0003, T10): a real
Incident -> RCA -> RemediationProposal -> Action chain (same reasoning as
T9's approval tests — the properties here, especially idempotency and the
resulting incident transitions, are only real when proven against a real
database), with a fake `kubectl` runner standing in for the real
subprocess call so these tests don't need a live cluster. Real-cluster
behavior (an actual `kubectl rollout undo` against a real Deployment) is
verified separately, by hand, against a live kind cluster — the same
precedent T7's own live-cluster note set.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from aic_agents.execution import (
    ExecutionError,
    ServiceAccountCredentials,
    dry_run_action,
    execute_action,
    patch_config,
    rollback_deployment,
)
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_common.errors import IllegalStateError, NotFoundError
from aic_database.models import (
    RCA,
    Action,
    ExecutionRecord,
    Incident,
    IncidentEvent,
    RemediationProposal,
)
from aic_domain.actions import ConfigChange, PatchConfigParams, RollbackDeploymentParams
from aic_domain.enums import ActionStatus, ActionType, ExecutionStatus, IncidentStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)

FAKE_CREDENTIALS = ServiceAccountCredentials(
    server="https://fake-k8s.invalid:6443",
    ca_cert_path=Path("/dev/null"),
    token="fake-executor-token",
    namespace="aic-demo",
)


def _seed_approved_action(
    session: Session,
    *,
    action_type: str = "RollbackDeployment",
    params: dict[str, object] | None = None,
    action_status: str = ActionStatus.APPROVED.value,
    incident_status: IncidentStatus = IncidentStatus.REMEDIATING,
) -> tuple[UUID, UUID]:
    """Seed a real Incident -> RCA -> RemediationProposal -> Action chain
    and return (incident_id, action_id)."""
    service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        service=service,
        environment=Environment.PROD,
        status=incident_status,
        created_at=T0,
    )
    session.add(incident)
    session.flush()

    rca = RCA(incident_id=incident.id, agent_version="test", status="draft", created_at=T0)
    session.add(rca)
    session.flush()

    proposal = RemediationProposal(
        incident_id=incident.id, rca_id=rca.id, rationale="r", created_at=T0
    )
    session.add(proposal)
    session.flush()

    action = Action(
        proposal_id=proposal.id,
        action_type=action_type,
        params=params
        if params is not None
        else {"deployment": service, "from_version": "v42", "to_version": "v41"},
        target_resource=service,
        status=action_status,
        idempotency_key=f"{incident.id}:{rca.id}:{action_type}",
        created_at=T0,
    )
    session.add(action)
    session.commit()
    return incident.id, action.id


def _event_types(session: Session, incident_id: UUID) -> list[str]:
    events = list(
        session.execute(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(IncidentEvent.seq)
        )
        .scalars()
        .all()
    )
    return [e.event_type for e in events]


class _CountingRunner:
    """Fake `kubectl` runner: records every invocation's argv and returns a
    fixed exit code, so tests can assert exactly what was run and how many
    times, without a real cluster."""

    def __init__(self, *, returncode: int = 0, stdout: str = "ok", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return subprocess.CompletedProcess(
            args, returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )


async def test_execute_action_succeeds_and_transitions_to_verifying(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, action_id = _seed_approved_action(session)

    runner = _CountingRunner(returncode=0, stdout="deployment.apps/x rolled back")
    with session_factory() as session:
        record = await execute_action(
            session,
            action_id,
            credentials=FAKE_CREDENTIALS,
            clock=FixedClock(T0),
            runner=runner,
        )
        session.commit()
        record_id = record.id

    assert len(runner.calls) == 1
    assert "--dry-run=server" not in runner.calls[0]
    args = runner.calls[0]
    # Regression guard (T10 live-cluster finding): without `--kubeconfig
    # /dev/null`, `kubectl` falls back to the operator's ambient
    # kubeconfig, and if it has a context pointing at the same API server
    # (as `kind create cluster` writes), the connection silently
    # authenticates via THAT context's client certificate instead of the
    # scoped `--token` below — defeating privilege separation entirely
    # while looking, from here, like a correctly-scoped call. Only a real
    # cluster with a real ambient kubeconfig exposed this; this assertion
    # is the cheapest guard against it regressing.
    assert args[:3] == ["kubectl", "--kubeconfig", "/dev/null"]
    assert "--server" in args
    assert "--certificate-authority" in args
    assert "rollout" in args
    assert "undo" in args

    with session_factory() as session:
        action = session.get(Action, action_id)
        incident = session.get(Incident, incident_id)
        exec_record = session.get(ExecutionRecord, record_id)
        assert action is not None
        assert incident is not None
        assert exec_record is not None
        assert action.status == ActionStatus.EXECUTED.value
        assert incident.status == IncidentStatus.VERIFYING
        assert exec_record.status == ExecutionStatus.SUCCEEDED.value
        assert exec_record.finished_at is not None
        assert exec_record.output is not None
        assert exec_record.output["returncode"] == 0
        assert _event_types(session, incident_id) == ["action_executed", "actions_executed"]


async def test_execute_action_failure_transitions_to_failed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, action_id = _seed_approved_action(session)

    runner = _CountingRunner(returncode=1, stderr="deployments.apps not found")
    with session_factory() as session:
        record = await execute_action(
            session,
            action_id,
            credentials=FAKE_CREDENTIALS,
            clock=FixedClock(T0),
            runner=runner,
        )
        session.commit()
        record_id = record.id

    with session_factory() as session:
        action = session.get(Action, action_id)
        incident = session.get(Incident, incident_id)
        exec_record = session.get(ExecutionRecord, record_id)
        assert action is not None
        assert incident is not None
        assert exec_record is not None
        assert action.status == ActionStatus.EXECUTION_FAILED.value
        assert incident.status == IncidentStatus.FAILED
        assert exec_record.status == ExecutionStatus.FAILED.value
        assert exec_record.output is not None
        assert "not found" in exec_record.output["error"]
        assert _event_types(session, incident_id) == [
            "action_execution_failed",
            "fatal_execution_error",
        ]


async def test_execute_action_is_idempotent_on_retry(
    session_factory: sessionmaker[Session],
) -> None:
    """A second call for the same action_id — simulating a retried poller
    invocation, e.g. after a crash-restart — must not invoke kubectl again
    and must not re-transition the incident (design doc §1.14 idempotency
    row, T10's own "Done when" bullet)."""
    with session_factory() as session:
        incident_id, action_id = _seed_approved_action(session)

    runner = _CountingRunner(returncode=0)
    with session_factory() as session:
        first = await execute_action(
            session, action_id, credentials=FAKE_CREDENTIALS, clock=FixedClock(T0), runner=runner
        )
        session.commit()
        first_id = first.id

    with session_factory() as session:
        second = await execute_action(
            session, action_id, credentials=FAKE_CREDENTIALS, clock=FixedClock(T0), runner=runner
        )
        session.commit()
        second_id = second.id

    assert first_id == second_id
    assert len(runner.calls) == 1  # kubectl invoked exactly once, not twice

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.VERIFYING
        # No duplicate audit rows from the second, no-op call.
        assert _event_types(session, incident_id) == ["action_executed", "actions_executed"]
        records = list(
            session.execute(
                select(ExecutionRecord).where(ExecutionRecord.action_id == action_id)
            )
            .scalars()
            .all()
        )
        assert len(records) == 1


async def test_execute_action_rejects_an_action_that_is_not_approved(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _incident_id, action_id = _seed_approved_action(
            session, action_status=ActionStatus.PENDING_APPROVAL.value
        )

    runner = _CountingRunner()
    with session_factory() as session, pytest.raises(IllegalStateError):
        await execute_action(
            session, action_id, credentials=FAKE_CREDENTIALS, clock=FixedClock(T0), runner=runner
        )
    assert runner.calls == []


async def test_execute_action_raises_not_found_for_unknown_action(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session, pytest.raises(NotFoundError):
        await execute_action(
            session,
            uuid4(),
            credentials=FAKE_CREDENTIALS,
            clock=FixedClock(T0),
            runner=_CountingRunner(),
        )


async def test_execute_action_patch_config_reverts_every_changed_key(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _incident_id, action_id = _seed_approved_action(
            session,
            action_type="PatchConfig",
            params={
                "deployment": "payment-service",
                "changes": [{"key": "DB_POOL_SIZE", "from_value": "3", "to_value": "20"}],
            },
        )

    runner = _CountingRunner(returncode=0)
    with session_factory() as session:
        await execute_action(
            session, action_id, credentials=FAKE_CREDENTIALS, clock=FixedClock(T0), runner=runner
        )
        session.commit()

    assert len(runner.calls) == 1
    args = runner.calls[0]
    assert "set" in args
    assert "env" in args
    assert "DB_POOL_SIZE=20" in args


async def test_rollback_deployment_dry_run_adds_flag_and_does_not_mutate_db(
    session_factory: sessionmaker[Session],
) -> None:
    runner = _CountingRunner(returncode=0, stdout="deployment.apps/x rolled back (dry run)")
    output = await dry_run_action(
        ActionType.ROLLBACK_DEPLOYMENT,
        {"deployment": "payment-service", "from_version": "v42", "to_version": "v41"},
        FAKE_CREDENTIALS,
        runner=runner,
    )
    assert output["returncode"] == 0
    assert len(runner.calls) == 1
    assert "--dry-run=server" in runner.calls[0]


async def test_rollback_deployment_raises_execution_error_on_nonzero_exit() -> None:
    runner = _CountingRunner(returncode=1, stderr="boom")
    with pytest.raises(ExecutionError, match="boom"):
        await rollback_deployment(
            FAKE_CREDENTIALS,
            RollbackDeploymentParams(deployment="x", from_version="v2", to_version="v1"),
            dry_run=False,
            runner=runner,
        )


async def test_patch_config_bundles_every_changed_key_in_one_command() -> None:
    runner = _CountingRunner(returncode=0)
    params = PatchConfigParams(
        deployment="payment-service",
        changes=[
            ConfigChange(key="DB_POOL_SIZE", from_value="3", to_value="20"),
            ConfigChange(key="DB_TIMEOUT_MS", from_value="100", to_value="500"),
        ],
    )
    await patch_config(FAKE_CREDENTIALS, params, dry_run=False, runner=runner)
    args = runner.calls[0]
    assert "DB_POOL_SIZE=20" in args
    assert "DB_TIMEOUT_MS=500" in args
