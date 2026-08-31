"""Approval workflow orchestration (design doc §1.10, T9).

`aic_domain.approval` decides *what* an approval request's outcome is from
a set of already-eligible decisions; this module is the Postgres-backed
*how*: recording one decider's decision, evaluating quorum against the
current DB state, and expiring a request nobody ever decided on. Both entry
points assume they run as the very first operation against a fresh
`Session` (same calling convention as `aic_agents.remediation
.plan_remediation`), since `record_decision` sets `SERIALIZABLE` isolation
on the session's connection before any query — Postgres requires that be
set before any statement in the transaction runs.

Design calls the doc doesn't pin down:

- A decision attempt against an already-expired-but-not-yet-swept request
  (`status` still `pending` because the expiry poller,
  `apps/aic-approval-expirer`, hasn't run yet) is rejected outright rather
  than silently accepted. It deliberately does **not** perform the
  expiry+escalation transition itself — raising inside a caller's
  transaction only for that transaction to roll back on the way out would
  discard the very state change it just made. Expiring a request that
  nobody ever revisits (so this rejection path never runs at all) is
  exactly why the dedicated poller exists.
- A decider without any of `required_roles` is rejected with
  `AuthorizationError` before their vote is ever written — an ineligible
  vote is never a real fact about this approval request, so it never
  becomes a persisted `ApprovalDecision` row (`aic_domain.approval`'s
  `evaluate_outcome` relies on this: every persisted decision is already
  known-eligible).
- The Postgres unique constraint on `(approval_request_id, decider_id)`
  (T9's migration) is the actual enforcement of "one vote per decider" —
  the `IntegrityError` it raises on a repeat vote is translated to
  `IllegalStateError` here rather than pre-checked, since a pre-check
  would itself be a race under concurrent decisions.
"""

from __future__ import annotations

from uuid import UUID

from aic_common.clock import Clock
from aic_common.errors import AuthorizationError, IllegalStateError, NotFoundError
from aic_common.ids import new_id
from aic_database.models import Action as ActionRow
from aic_database.models import ApprovalDecision as ApprovalDecisionRow
from aic_database.models import ApprovalRequest as ApprovalRequestRow
from aic_database.models import Incident as IncidentRow
from aic_database.models import IncidentEvent
from aic_database.models import RemediationProposal as RemediationProposalRow
from aic_domain.approval import evaluate_outcome, is_eligible
from aic_domain.enums import (
    ActionStatus,
    ActorType,
    ApprovalDecisionType,
    ApprovalRequestStatus,
    IncidentTransitionEvent,
)
from aic_domain.state_machine import transition
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _next_seq(session: Session, incident_id: UUID) -> int:
    stmt = select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
        IncidentEvent.incident_id == incident_id
    )
    result: int = session.execute(stmt).scalar_one()
    return result + 1


def load_incident_for_action(session: Session, action_id: UUID) -> IncidentRow:
    """Public (not `_`-prefixed): `aic_approval_api` also needs this lookup
    to report the resulting incident state in its HTTP response, and
    re-deriving the same Action -> RemediationProposal -> Incident join in
    a second place would risk it silently drifting from this one."""
    incident_id = session.execute(
        select(RemediationProposalRow.incident_id)
        .join(ActionRow, ActionRow.proposal_id == RemediationProposalRow.id)
        .where(ActionRow.id == action_id)
    ).scalar_one()
    incident = session.get(IncidentRow, incident_id)
    if incident is None:
        raise NotFoundError(f"action {action_id} references a nonexistent incident")
    return incident


def record_decision(
    session: Session,
    approval_request_id: UUID,
    *,
    decider_id: str,
    decider_roles: frozenset[str],
    decision: ApprovalDecisionType,
    reason: str | None,
    clock: Clock,
) -> ApprovalDecisionRow:
    """Cast one decider's vote and, if it resolves the request, transition
    the incident. Must run as the first operation on `session` (see module
    docstring).

    That precondition is checked, not just documented: if `session`'s
    connection/transaction was already established by an earlier statement,
    Postgres silently keeps the isolation level it started with and
    SQLAlchemy only *warns* — it does not raise — so a violation would
    otherwise pass every test that doesn't specifically provoke the race.
    Reading the isolation level back and failing loudly turns that into a
    real, enforced invariant instead of a comment someone can forget.
    """
    connection = session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
    if connection.get_isolation_level() != "SERIALIZABLE":
        raise IllegalStateError(
            "record_decision() must run as the first operation on a fresh session — "
            "this session's connection was already established at a different "
            "isolation level"
        )

    request = session.get(ApprovalRequestRow, approval_request_id)
    if request is None:
        raise NotFoundError(f"no approval request with id {approval_request_id}")
    if request.status != ApprovalRequestStatus.PENDING.value:
        raise IllegalStateError(
            f"approval request {approval_request_id} is not pending (status={request.status!r})"
        )
    if clock.now() >= request.expires_at:
        raise IllegalStateError(f"approval request {approval_request_id} has expired")

    required_roles = frozenset(request.required_roles)
    if not is_eligible(required_roles, decider_roles):
        raise AuthorizationError(
            f"decider {decider_id!r} lacks required role(s) {sorted(required_roles)}"
        )

    decision_row = ApprovalDecisionRow(
        id=new_id(),
        approval_request_id=approval_request_id,
        decider_id=decider_id,
        decision=decision,
        reason=reason,
        decided_at=clock.now(),
    )
    session.add(decision_row)
    try:
        session.flush()
    except IntegrityError as exc:
        raise IllegalStateError(
            f"decider {decider_id!r} has already cast a decision on approval "
            f"request {approval_request_id}"
        ) from exc

    incident = load_incident_for_action(session, request.action_id)
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            seq=_next_seq(session, incident.id),
            event_type="approval_decision_recorded",
            actor_type=ActorType.HUMAN,
            actor_id=decider_id,
            payload={
                "approval_request_id": str(approval_request_id),
                "decision": decision.value,
                "reason": reason,
            },
            created_at=clock.now(),
        )
    )

    cast_decisions = list(
        session.execute(
            select(ApprovalDecisionRow.decision).where(
                ApprovalDecisionRow.approval_request_id == approval_request_id
            )
        )
        .scalars()
        .all()
    )
    outcome = evaluate_outcome(request.quorum, cast_decisions)

    if outcome == ApprovalRequestStatus.APPROVED:
        request.status = ApprovalRequestStatus.APPROVED.value
        action = session.get(ActionRow, request.action_id)
        assert action is not None
        # T8's auto-approve path sets this directly; the require-approval
        # path only reaches "approved" here, at quorum, so it must set it
        # too — otherwise Action.status would stay `pending_approval`
        # forever on this path, and T10's executor (which finds work by
        # `Action.status == APPROVED`) would never see it.
        action.status = ActionStatus.APPROVED.value
        incident.status = transition(incident.status, IncidentTransitionEvent.QUORUM_MET)
        session.add(
            IncidentEvent(
                incident_id=incident.id,
                seq=_next_seq(session, incident.id),
                event_type=IncidentTransitionEvent.QUORUM_MET.value,
                actor_type=ActorType.SYSTEM,
                payload={"approval_request_id": str(approval_request_id)},
                created_at=clock.now(),
            )
        )
    elif outcome == ApprovalRequestStatus.REJECTED:
        request.status = ApprovalRequestStatus.REJECTED.value
        incident.status = transition(incident.status, IncidentTransitionEvent.REJECTED)
        session.add(
            IncidentEvent(
                incident_id=incident.id,
                seq=_next_seq(session, incident.id),
                event_type=IncidentTransitionEvent.REJECTED.value,
                actor_type=ActorType.SYSTEM,
                payload={"approval_request_id": str(approval_request_id)},
                created_at=clock.now(),
            )
        )

    return decision_row


def expire_request(session: Session, approval_request_id: UUID, *, clock: Clock) -> None:
    """Expire a request nobody decided on in time. Idempotent: a request
    that is no longer `pending` (already decided, or already expired by an
    earlier poll) is a silent no-op, matching this project's other
    poller-facing operations (e.g. T4's alert-event dedup)."""
    request = session.get(ApprovalRequestRow, approval_request_id)
    if request is None:
        raise NotFoundError(f"no approval request with id {approval_request_id}")
    if request.status != ApprovalRequestStatus.PENDING.value:
        return
    if clock.now() < request.expires_at:
        raise IllegalStateError(f"approval request {approval_request_id} has not yet expired")

    request.status = ApprovalRequestStatus.EXPIRED.value
    incident = load_incident_for_action(session, request.action_id)
    incident.status = transition(incident.status, IncidentTransitionEvent.EXPIRED)
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            seq=_next_seq(session, incident.id),
            event_type=IncidentTransitionEvent.EXPIRED.value,
            actor_type=ActorType.SYSTEM,
            payload={"approval_request_id": str(approval_request_id)},
            created_at=clock.now(),
        )
    )
