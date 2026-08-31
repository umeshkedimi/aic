"""Approval workflow domain logic (design doc §1.10, T9).

Pure decision logic only — no I/O. `aic_agents.approval` owns the
Postgres-backed orchestration (recording decisions, evaluating quorum in a
real transaction, expiring stale requests).

Eligibility is checked once, at the moment a decision is about to be
persisted (`aic_agents.approval.record_decision`): a decider without any of
`required_roles` is rejected outright and never written as a decision row.
So `evaluate_outcome` below only ever sees decisions that were already
eligible when cast, and the Postgres unique constraint on
`(approval_request_id, decider_id)` (same migration as the immutability
trigger) guarantees at most one decision per decider — so a plain count of
`APPROVE` decisions is already a distinct-decider count, with no need to
re-check roles or dedupe here.

Design call the doc doesn't pin down: §6's state machine only has a single
`rejected` edge out of `awaiting_approval`, with no "N rejects needed"
concept alongside `quorum_met` — so one REJECT from an eligible decider
ends the request immediately regardless of quorum. Quorum is a bar for
*proceeding*, not for *stopping*; that's also the safer default for a
gate in front of a real production action.
"""

from __future__ import annotations

from datetime import timedelta

from aic_domain.enums import ApprovalDecisionType, ApprovalRequestStatus

# Not specified by the design doc's own worked example (quorum=1, role=sre,
# "expiry + escalation ladder" — no duration given). A bounded, documented
# default rather than a request that can sit open forever.
DEFAULT_APPROVAL_EXPIRY = timedelta(minutes=30)


def is_eligible(required_roles: frozenset[str], decider_roles: frozenset[str]) -> bool:
    """A request with no `required_roles` is open to any authenticated
    decider; otherwise the decider must hold at least one required role."""
    if not required_roles:
        return True
    return bool(required_roles & decider_roles)


def evaluate_outcome(quorum: int, decisions: list[ApprovalDecisionType]) -> ApprovalRequestStatus:
    """Compute the request's status from the decisions cast so far.

    `decisions` must contain only already-eligible votes (see module
    docstring) — this function does no role-checking itself.
    """
    if ApprovalDecisionType.REJECT in decisions:
        return ApprovalRequestStatus.REJECTED
    approvals = sum(1 for d in decisions if d == ApprovalDecisionType.APPROVE)
    if approvals >= quorum:
        return ApprovalRequestStatus.APPROVED
    return ApprovalRequestStatus.PENDING
