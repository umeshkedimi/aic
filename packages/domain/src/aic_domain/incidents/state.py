"""The incident lifecycle, as a pure state machine.

`transition` is the *only* function permitted to compute the next status.
The API and the (future) investigation workflow both call it instead of
setting `incident.status` directly, so the audit spine (an IncidentEvent
per transition) and the status column can never disagree.

Mirrors docs/design/14-state-management.md §14.4.
"""

from __future__ import annotations

from enum import StrEnum

from aic_domain.incidents.errors import IllegalTransition


class IncidentStatus(StrEnum):
    OPEN = "open"
    TRIAGING = "triaging"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    REMEDIATING = "remediating"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ESCALATED = "escalated"
    FAILED = "failed"


class IncidentTransitionEvent(StrEnum):
    WORKFLOW_STARTED = "workflow_started"
    TRIAGE_COMPLETED = "triage_completed"
    PROPOSAL_REQUIRES_APPROVAL = "proposal_requires_approval"
    ALL_ACTIONS_AUTO_APPROVED = "all_actions_auto_approved"
    BUDGET_EXHAUSTED = "budget_exhausted"
    HUMAN_TAKEOVER = "human_takeover"
    QUORUM_MET = "quorum_met"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    ACTIONS_EXECUTED = "actions_executed"
    FATAL_EXECUTION_ERROR = "fatal_execution_error"
    SOAK_PASSED = "soak_passed"
    ROLLBACK = "rollback"
    VERIFICATION_FAILED_NO_ROLLBACK = "verification_failed_no_rollback"
    HUMAN_RESOLVED = "human_resolved"
    POST_REVIEW = "post_review"
    ESCALATE = "escalate"


_TRANSITIONS: dict[IncidentStatus, dict[IncidentTransitionEvent, IncidentStatus]] = {
    IncidentStatus.OPEN: {
        IncidentTransitionEvent.WORKFLOW_STARTED: IncidentStatus.TRIAGING,
    },
    IncidentStatus.TRIAGING: {
        IncidentTransitionEvent.TRIAGE_COMPLETED: IncidentStatus.INVESTIGATING,
    },
    IncidentStatus.INVESTIGATING: {
        IncidentTransitionEvent.PROPOSAL_REQUIRES_APPROVAL: IncidentStatus.AWAITING_APPROVAL,
        IncidentTransitionEvent.ALL_ACTIONS_AUTO_APPROVED: IncidentStatus.REMEDIATING,
        IncidentTransitionEvent.BUDGET_EXHAUSTED: IncidentStatus.ESCALATED,
        IncidentTransitionEvent.HUMAN_TAKEOVER: IncidentStatus.ESCALATED,
    },
    IncidentStatus.AWAITING_APPROVAL: {
        IncidentTransitionEvent.QUORUM_MET: IncidentStatus.REMEDIATING,
        IncidentTransitionEvent.APPROVAL_REJECTED: IncidentStatus.ESCALATED,
        IncidentTransitionEvent.APPROVAL_EXPIRED: IncidentStatus.ESCALATED,
    },
    IncidentStatus.REMEDIATING: {
        IncidentTransitionEvent.ACTIONS_EXECUTED: IncidentStatus.VERIFYING,
        IncidentTransitionEvent.FATAL_EXECUTION_ERROR: IncidentStatus.FAILED,
    },
    IncidentStatus.VERIFYING: {
        IncidentTransitionEvent.SOAK_PASSED: IncidentStatus.RESOLVED,
        IncidentTransitionEvent.ROLLBACK: IncidentStatus.REMEDIATING,
        IncidentTransitionEvent.VERIFICATION_FAILED_NO_ROLLBACK: IncidentStatus.ESCALATED,
    },
    IncidentStatus.ESCALATED: {
        IncidentTransitionEvent.HUMAN_RESOLVED: IncidentStatus.RESOLVED,
    },
    IncidentStatus.RESOLVED: {
        IncidentTransitionEvent.POST_REVIEW: IncidentStatus.CLOSED,
    },
    IncidentStatus.FAILED: {
        IncidentTransitionEvent.ESCALATE: IncidentStatus.ESCALATED,
    },
    IncidentStatus.CLOSED: {},
}


def transition(
    current: IncidentStatus, event: IncidentTransitionEvent
) -> IncidentStatus:
    """Compute the next status, or raise IllegalTransition.

    Pure function: no I/O, no clock, no randomness — safe to call from
    anywhere (API handlers today; a workflow engine's activities later)
    without smuggling in a durability or determinism concern.
    """
    next_status = _TRANSITIONS.get(current, {}).get(event)
    if next_status is None:
        raise IllegalTransition(current=current, event=event)
    return next_status
