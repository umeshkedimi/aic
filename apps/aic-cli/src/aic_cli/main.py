"""`aic` operator CLI (design doc §1.10 APPROVE row, T9): `aic approve
<incident-id>`, the one-command v1 surface alongside `aic-approval-api`'s
HTTP endpoint. Both call the same `aic_agents.approval.record_decision`.

Unlike the API, this CLI does not authenticate over a network — it talks
directly to Postgres, the same trust model `apps/toy-ops`'s own scripts
already use (whoever can run it on a machine with `AIC_DATABASE_URL`
already has that access). The decider's identity/roles for the decision
being cast come from the environment (`AIC_CLI_DECIDER_ID`,
`AIC_CLI_DECIDER_ROLES`) rather than a login step, for the same reason.
"""

from __future__ import annotations

import argparse
import os
import sys
from uuid import UUID

from aic_agents.approval import load_incident_for_action, record_decision
from aic_common.clock import SystemClock
from aic_common.errors import AICError
from aic_database.models import Action, ApprovalRequest, RemediationProposal
from aic_database.session import DatabaseSettings, create_database_engine, create_session_factory
from aic_domain.enums import ApprovalDecisionType, ApprovalRequestStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def find_pending_approval_request_id(session: Session, incident_id: UUID) -> UUID | None:
    """The most recently created still-pending request for this incident.
    In this scenario's rule table an incident has at most one, but "most
    recent" is the sane tie-break if that were ever not true."""
    result: UUID | None = session.execute(
        select(ApprovalRequest.id)
        .join(Action, Action.id == ApprovalRequest.action_id)
        .join(RemediationProposal, RemediationProposal.id == Action.proposal_id)
        .where(RemediationProposal.incident_id == incident_id)
        .where(ApprovalRequest.status == ApprovalRequestStatus.PENDING.value)
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return result


def _decider_roles_from_env() -> frozenset[str]:
    raw = os.environ.get("AIC_CLI_DECIDER_ROLES", "")
    return frozenset(role.strip() for role in raw.split(",") if role.strip())


def approve(
    session_factory: sessionmaker[Session],
    incident_id: UUID,
    *,
    decider_id: str,
    decider_roles: frozenset[str],
    reason: str | None,
) -> tuple[str, str]:
    """Returns (approval_status, incident_status). Raises `AICError` on
    failure (unknown incident/no pending request/not eligible/etc.).

    The lookup and the decision run in two separate sessions/transactions,
    deliberately: `record_decision` sets `SERIALIZABLE` isolation on its
    session's connection, which Postgres only accepts before any statement
    has run in that transaction. Looking up the request id first on the
    *same* session that then calls `record_decision` would make that
    connection already-established by the time `record_decision` tries to
    set it, and SQLAlchemy would silently ignore the isolation-level
    request rather than error — exactly the kind of silent correctness gap
    this project doesn't accept.
    """
    with session_factory() as lookup_session:
        request_id = find_pending_approval_request_id(lookup_session, incident_id)
    if request_id is None:
        raise AICError(f"incident {incident_id} has no pending approval request")

    with session_factory() as session:
        record_decision(
            session,
            request_id,
            decider_id=decider_id,
            decider_roles=decider_roles,
            decision=ApprovalDecisionType.APPROVE,
            reason=reason,
            clock=SystemClock(),
        )
        request = session.get(ApprovalRequest, request_id)
        assert request is not None
        incident = load_incident_for_action(session, request.action_id)
        session.commit()
        return request.status, incident.status.value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aic")
    subparsers = parser.add_subparsers(dest="command", required=True)

    approve_parser = subparsers.add_parser(
        "approve", help="Approve the pending remediation action for an incident."
    )
    approve_parser.add_argument("incident_id", type=UUID)
    approve_parser.add_argument("--reason", default=None)

    args = parser.parse_args(argv)

    decider_id = os.environ.get("AIC_CLI_DECIDER_ID")
    if not decider_id:
        print("AIC_CLI_DECIDER_ID must be set to identify the approving human", file=sys.stderr)
        return 2

    db_settings = DatabaseSettings(url=os.environ["AIC_DATABASE_URL"])
    engine = create_database_engine(db_settings)
    session_factory = create_session_factory(engine)
    try:
        approval_status, incident_status = approve(
            session_factory,
            args.incident_id,
            decider_id=decider_id,
            decider_roles=_decider_roles_from_env(),
            reason=args.reason,
        )
    except AICError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(f"approval {approval_status}; incident {args.incident_id} is now {incident_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
