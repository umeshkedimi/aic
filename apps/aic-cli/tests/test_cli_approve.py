from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from aic_cli.main import approve, main
from aic_common.config import Environment
from aic_common.errors import AICError
from aic_database.models import RCA, Action, ApprovalRequest, Incident, RemediationProposal
from aic_domain.enums import ApprovalRequestStatus, IncidentStatus
from sqlalchemy.orm import Session, sessionmaker


def _seed_pending_approval(
    session: Session, *, required_roles: list[str] | None = None
) -> tuple[UUID, UUID]:
    now = datetime.now(UTC)
    service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        service=service,
        environment=Environment.PROD,
        status=IncidentStatus.AWAITING_APPROVAL,
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
        required_roles=required_roles if required_roles is not None else ["sre"],
        expires_at=now + timedelta(minutes=30),
        status=ApprovalRequestStatus.PENDING.value,
        created_at=now,
    )
    session.add(request)
    session.commit()
    return incident.id, request.id


def test_approve_happy_path(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        incident_id, _request_id = _seed_pending_approval(session)

    approval_status, incident_status = approve(
        session_factory,
        incident_id,
        decider_id="alice",
        decider_roles=frozenset({"sre"}),
        reason="cli approval",
    )
    assert approval_status == "approved"
    assert incident_status == "remediating"


def test_approve_raises_when_incident_has_no_pending_request(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(AICError, match="no pending approval request"):
        approve(
            session_factory,
            uuid4(),
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            reason=None,
        )


def test_approve_raises_when_decider_lacks_required_role(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _request_id = _seed_pending_approval(session, required_roles=["sre"])

    with pytest.raises(AICError, match="lacks required role"):
        approve(
            session_factory,
            incident_id,
            decider_id="carol",
            decider_roles=frozenset({"support"}),
            reason=None,
        )


def test_main_end_to_end_approves_via_env_configured_identity(
    postgres_url: str,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with session_factory() as session:
        incident_id, _request_id = _seed_pending_approval(session)

    monkeypatch.setenv("AIC_DATABASE_URL", postgres_url)
    monkeypatch.setenv("AIC_CLI_DECIDER_ID", "alice")
    monkeypatch.setenv("AIC_CLI_DECIDER_ROLES", "sre")

    exit_code = main(["approve", str(incident_id)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "approved" in out
    assert "remediating" in out


def test_main_requires_decider_id_env_var(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AIC_DATABASE_URL", postgres_url)
    monkeypatch.delenv("AIC_CLI_DECIDER_ID", raising=False)

    exit_code = main(["approve", str(uuid4())])

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "AIC_CLI_DECIDER_ID" in err
