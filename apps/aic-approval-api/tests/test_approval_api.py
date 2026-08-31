from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from aic_approval_api.config import ApprovalApiSettings, DeciderIdentity
from aic_approval_api.main import create_app
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import (
    RCA,
    Action,
    ApprovalRequest,
    Incident,
    RemediationProposal,
)
from aic_domain.enums import ApprovalRequestStatus, IncidentStatus
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker


def _seed_pending_approval(
    session: Session,
    *,
    quorum: int = 1,
    required_roles: list[str] | None = None,
    dry_run_result: dict[str, object] | None = None,
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
        params={"deployment": service, "from_version": "v42", "to_version": "v41"},
        target_resource=service,
        status="pending_approval",
        idempotency_key=f"{incident.id}:{rca.id}:RollbackDeployment",
        created_at=now,
    )
    session.add(action)
    session.flush()
    request = ApprovalRequest(
        action_id=action.id,
        quorum=quorum,
        required_roles=required_roles if required_roles is not None else ["sre"],
        expires_at=now + timedelta(minutes=30),
        status=ApprovalRequestStatus.PENDING.value,
        created_at=now,
        dry_run_result=dry_run_result,
    )
    session.add(request)
    session.commit()
    return incident.id, request.id


def _app(session_factory: sessionmaker[Session]) -> FastAPI:
    settings = ApprovalApiSettings(
        identities={
            "tok_alice": DeciderIdentity(decider_id="alice", roles=["sre"]),
            "tok_carol": DeciderIdentity(decider_id="carol", roles=["support"]),
        }
    )
    return create_app(
        settings=settings,
        session_factory=session_factory,
        clock=FixedClock(datetime.now(UTC)),
    )


def test_health(session_factory: sessionmaker[Session]) -> None:
    with TestClient(_app(session_factory)) as client:
        resp = client.get("/health")
    assert resp.status_code == 200


def test_get_card_returns_the_approval_details_including_dry_run(
    session_factory: sessionmaker[Session],
) -> None:
    dry_run = {"command": ["kubectl", "rollout", "undo"], "returncode": 0}
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(session, dry_run_result=dry_run)

    with TestClient(_app(session_factory)) as client:
        resp = client.get(f"/approvals/{request_id}", headers={"Authorization": "Bearer tok_alice"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_request_id"] == str(request_id)
    assert body["incident_id"] == str(incident_id)
    assert body["status"] == "pending"
    assert body["action_type"] == "RollbackDeployment"
    assert body["dry_run_result"] == dry_run


def test_get_card_without_a_dry_run_returns_null(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session)

    with TestClient(_app(session_factory)) as client:
        resp = client.get(f"/approvals/{request_id}", headers={"Authorization": "Bearer tok_alice"})

    assert resp.status_code == 200
    assert resp.json()["dry_run_result"] is None


def test_get_card_requires_authentication(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session)

    with TestClient(_app(session_factory)) as client:
        resp = client.get(f"/approvals/{request_id}")
    assert resp.status_code == 401


def test_get_card_unknown_request_is_not_found(session_factory: sessionmaker[Session]) -> None:
    with TestClient(_app(session_factory)) as client:
        resp = client.get(f"/approvals/{uuid4()}", headers={"Authorization": "Bearer tok_alice"})
    assert resp.status_code == 404


def test_approve_meets_quorum_and_returns_resulting_state(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(session, quorum=1)

    with TestClient(_app(session_factory)) as client:
        resp = client.post(
            f"/approvals/{request_id}/decision",
            json={"decision": "approve", "reason": "looks right"},
            headers={"Authorization": "Bearer tok_alice"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_request_id"] == str(request_id)
    assert body["approval_status"] == "approved"
    assert body["incident_id"] == str(incident_id)
    assert body["incident_status"] == "remediating"


def test_reject_escalates(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session, quorum=1)

    with TestClient(_app(session_factory)) as client:
        resp = client.post(
            f"/approvals/{request_id}/decision",
            json={"decision": "reject", "reason": "too risky"},
            headers={"Authorization": "Bearer tok_alice"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "rejected"
    assert body["incident_status"] == "escalated"


def test_missing_token_is_unauthorized(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session)

    with TestClient(_app(session_factory)) as client:
        resp = client.post(f"/approvals/{request_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 401


def test_unknown_token_is_unauthorized(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session)

    with TestClient(_app(session_factory)) as client:
        resp = client.post(
            f"/approvals/{request_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": "Bearer nonexistent"},
        )
    assert resp.status_code == 401


def test_decider_without_required_role_is_forbidden(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session, required_roles=["sre"])

    with TestClient(_app(session_factory)) as client:
        resp = client.post(
            f"/approvals/{request_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": "Bearer tok_carol"},
        )
    assert resp.status_code == 403


def test_unknown_approval_request_is_not_found(session_factory: sessionmaker[Session]) -> None:
    with TestClient(_app(session_factory)) as client:
        resp = client.post(
            f"/approvals/{uuid4()}/decision",
            json={"decision": "approve"},
            headers={"Authorization": "Bearer tok_alice"},
        )
    assert resp.status_code == 404


def test_deciding_twice_on_an_already_decided_request_is_a_conflict(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session, quorum=1)

    with TestClient(_app(session_factory)) as client:
        first = client.post(
            f"/approvals/{request_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": "Bearer tok_alice"},
        )
        assert first.status_code == 200

        second = client.post(
            f"/approvals/{request_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": "Bearer tok_alice"},
        )
    assert second.status_code == 409
