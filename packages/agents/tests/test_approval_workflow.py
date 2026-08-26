"""Approval workflow orchestration (design doc §1.10, T9): recording a
decision, quorum evaluation, and expiry, all against a real Postgres shape
— a real `Incident`/`RCA`/`RemediationProposal`/`Action`/`ApprovalRequest`
chain, since the properties this task cares about (the immutability
trigger, the double-vote unique constraint, the `SERIALIZABLE` quorum
race) are only real when proven against a real database, not a mock.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from aic_agents.approval import expire_request, record_decision
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_common.errors import AuthorizationError, IllegalStateError, NotFoundError
from aic_database.models import (
    RCA,
    Action,
    ApprovalRequest,
    Incident,
    IncidentEvent,
    RemediationProposal,
)
from aic_domain.enums import ApprovalDecisionType, ApprovalRequestStatus, IncidentStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def _seed_pending_approval(
    session: Session,
    *,
    quorum: int = 1,
    required_roles: list[str] | None = None,
    expires_at: datetime | None = None,
    incident_status: IncidentStatus = IncidentStatus.AWAITING_APPROVAL,
) -> tuple[UUID, UUID]:
    """Seed a real Incident -> RCA -> RemediationProposal -> Action ->
    ApprovalRequest chain and return (incident_id, approval_request_id)."""
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
        quorum=quorum,
        required_roles=required_roles if required_roles is not None else ["sre"],
        expires_at=expires_at if expires_at is not None else now + timedelta(minutes=30),
        status=ApprovalRequestStatus.PENDING.value,
        created_at=now,
    )
    session.add(request)
    session.commit()
    return incident.id, request.id


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


def test_single_approval_meets_quorum_one_and_remediates(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(session, quorum=1)

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason="looks right",
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.APPROVED.value
        assert incident.status == IncidentStatus.REMEDIATING
        assert _event_types(session, incident_id) == [
            "approval_decision_recorded",
            "quorum_met",
        ]


def test_quorum_two_stays_pending_after_one_approval(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(session, quorum=2)

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason=None,
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.PENDING.value
        assert incident.status == IncidentStatus.AWAITING_APPROVAL
        assert _event_types(session, incident_id) == ["approval_decision_recorded"]

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id="bob",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason=None,
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.APPROVED.value
        assert incident.status == IncidentStatus.REMEDIATING


def test_reject_escalates_immediately_regardless_of_quorum(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(session, quorum=2)

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.REJECT,
            reason="risk too high",
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.REJECTED.value
        assert incident.status == IncidentStatus.ESCALATED
        assert _event_types(session, incident_id) == ["approval_decision_recorded", "rejected"]


def test_ineligible_decider_is_rejected_and_never_persisted(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session, required_roles=["sre"])

    with session_factory() as session:
        with pytest.raises(AuthorizationError, match="lacks required role"):
            record_decision(
                session,
                request_id,
                decider_id="carol",
                decider_roles=frozenset({"support"}),
                decision=ApprovalDecisionType.APPROVE,
                reason=None,
                clock=FixedClock(datetime.now(UTC)),
            )
        session.rollback()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        assert request is not None
        assert request.status == ApprovalRequestStatus.PENDING.value


def test_double_vote_by_the_same_decider_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session, quorum=2)

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason=None,
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(IllegalStateError, match="already cast a decision"):
            record_decision(
                session,
                request_id,
                decider_id="alice",
                decider_roles=frozenset({"sre"}),
                decision=ApprovalDecisionType.APPROVE,
                reason=None,
                clock=FixedClock(datetime.now(UTC)),
            )
        session.rollback()


def test_deciding_on_an_already_decided_request_raises(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(session, quorum=1)

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason=None,
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

    with session_factory() as session:
        with pytest.raises(IllegalStateError, match="not pending"):
            record_decision(
                session,
                request_id,
                decider_id="bob",
                decider_roles=frozenset({"sre"}),
                decision=ApprovalDecisionType.APPROVE,
                reason=None,
                clock=FixedClock(datetime.now(UTC)),
            )
        session.rollback()


def test_deciding_on_an_expired_request_raises_without_mutating_it(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(
            session, expires_at=now - timedelta(minutes=1)
        )

    with session_factory() as session:
        with pytest.raises(IllegalStateError, match="has expired"):
            record_decision(
                session,
                request_id,
                decider_id="alice",
                decider_roles=frozenset({"sre"}),
                decision=ApprovalDecisionType.APPROVE,
                reason=None,
                clock=FixedClock(now),
            )
        session.rollback()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        assert request is not None
        assert request.status == ApprovalRequestStatus.PENDING.value


def test_record_decision_raises_for_unknown_request(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session, pytest.raises(NotFoundError):
        record_decision(
            session,
            uuid4(),
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason=None,
            clock=FixedClock(datetime.now(UTC)),
        )


def test_expire_request_past_due_escalates_the_incident(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(
            session, expires_at=now - timedelta(seconds=1)
        )

    with session_factory() as session:
        expire_request(session, request_id, clock=FixedClock(now))
        session.commit()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.EXPIRED.value
        assert incident.status == IncidentStatus.ESCALATED
        assert _event_types(session, incident_id) == ["expired"]


def test_expire_request_not_yet_due_raises(session_factory: sessionmaker[Session]) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        _incident_id, request_id = _seed_pending_approval(
            session, expires_at=now + timedelta(minutes=30)
        )

    with session_factory() as session:
        with pytest.raises(IllegalStateError, match="not yet expired"):
            expire_request(session, request_id, clock=FixedClock(now))
        session.rollback()


def test_expire_request_is_idempotent_once_already_resolved(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(session, quorum=1)

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason=None,
            clock=FixedClock(now),
        )
        session.commit()

    # The request is already APPROVED, not pending — a later expiry sweep
    # (e.g. a slow poll cycle) must be a silent no-op, not an error.
    with session_factory() as session:
        expire_request(session, request_id, clock=FixedClock(now + timedelta(hours=1)))
        session.commit()

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.APPROVED.value
        assert incident.status == IncidentStatus.REMEDIATING


def test_expire_request_raises_for_unknown_request(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session, pytest.raises(NotFoundError):
        expire_request(session, uuid4(), clock=FixedClock(datetime.now(UTC)))


def test_concurrent_quorum_meeting_decisions_do_not_double_transition(
    session_factory: sessionmaker[Session],
) -> None:
    """Two different deciders both cast the vote that would meet quorum=2 at
    (as close to) the same moment. `SERIALIZABLE` isolation must ensure at
    most one of the two transactions actually flips the request to APPROVED
    and fires `quorum_met` — the other must fail outright (this project's
    "writes never auto-retry silently" convention: the loser's caller sees
    a real error, not a silently swallowed race) rather than both
    succeeding and double-transitioning the incident."""
    with session_factory() as session:
        incident_id, request_id = _seed_pending_approval(session, quorum=2)
        record_decision(
            session,
            request_id,
            decider_id="alice",
            decider_roles=frozenset({"sre"}),
            decision=ApprovalDecisionType.APPROVE,
            reason=None,
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

    results: list[str] = []

    def _cast(decider_id: str) -> None:
        with session_factory() as thread_session:
            try:
                record_decision(
                    thread_session,
                    request_id,
                    decider_id=decider_id,
                    decider_roles=frozenset({"sre"}),
                    decision=ApprovalDecisionType.APPROVE,
                    reason=None,
                    clock=FixedClock(datetime.now(UTC)),
                )
                thread_session.commit()
                results.append("ok")
            except Exception:
                thread_session.rollback()
                results.append("failed")

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(_cast, ["bob", "carol"]))

    with session_factory() as session:
        request = session.get(ApprovalRequest, request_id)
        incident = session.get(Incident, incident_id)
        assert request is not None
        assert incident is not None
        assert request.status == ApprovalRequestStatus.APPROVED.value
        assert incident.status == IncidentStatus.REMEDIATING
        assert _event_types(session, incident_id).count("quorum_met") == 1
        # Exactly one of the two racing deciders' votes was ever
        # recorded as a fact — the loser's attempt left no trace.
        assert sorted(results) == ["failed", "ok"]
