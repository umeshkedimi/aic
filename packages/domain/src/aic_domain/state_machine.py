"""The incident state machine (design doc §6).

`transition()` is the *only* function permitted to compute the next
`IncidentStatus` — the API layer and the workflow/graph code both call it
so `incident.status` can never drift from what the audit spine
(`IncidentEvent`) recorded. It is a pure function: given the same
`(current, event)` it always returns the same result or always raises, with
no I/O and no hidden state.

Note on the verification retry bound: the diagram's `verifying ->
investigating (1 retry max)` edge is always a legal transition here —
`transition()` has no memory of how many times it has already fired for a
given incident. Enforcing the "exactly one retry" bound is the caller's
job (T11's verification workflow, which has access to the incident's
history); baking a counter into this pure function would make it stateful
and untestable in isolation.
"""

from __future__ import annotations

from aic_common.errors import IllegalStateError

from aic_domain.enums import IncidentStatus, IncidentTransitionEvent

_TRANSITIONS: dict[tuple[IncidentStatus, IncidentTransitionEvent], IncidentStatus] = {
    (IncidentStatus.OPEN, IncidentTransitionEvent.WORKFLOW_STARTED): IncidentStatus.TRIAGING,
    (
        IncidentStatus.TRIAGING,
        IncidentTransitionEvent.TRIAGE_COMPLETED,
    ): IncidentStatus.INVESTIGATING,
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.PROPOSAL_REQUIRES_APPROVAL,
    ): IncidentStatus.AWAITING_APPROVAL,
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.ALL_ACTIONS_AUTO_APPROVED,
    ): IncidentStatus.REMEDIATING,
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.BUDGET_EXHAUSTED,
    ): IncidentStatus.ESCALATED,
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.HUMAN_TAKEOVER,
    ): IncidentStatus.ESCALATED,
    (
        IncidentStatus.AWAITING_APPROVAL,
        IncidentTransitionEvent.QUORUM_MET,
    ): IncidentStatus.REMEDIATING,
    (IncidentStatus.AWAITING_APPROVAL, IncidentTransitionEvent.REJECTED): IncidentStatus.ESCALATED,
    (IncidentStatus.AWAITING_APPROVAL, IncidentTransitionEvent.EXPIRED): IncidentStatus.ESCALATED,
    (
        IncidentStatus.REMEDIATING,
        IncidentTransitionEvent.ACTIONS_EXECUTED,
    ): IncidentStatus.VERIFYING,
    (
        IncidentStatus.REMEDIATING,
        IncidentTransitionEvent.FATAL_EXECUTION_ERROR,
    ): IncidentStatus.FAILED,
    (IncidentStatus.VERIFYING, IncidentTransitionEvent.SOAK_PASSED): IncidentStatus.RESOLVED,
    (
        IncidentStatus.VERIFYING,
        IncidentTransitionEvent.VERIFICATION_FAILED,
    ): IncidentStatus.INVESTIGATING,
    (
        IncidentStatus.VERIFYING,
        IncidentTransitionEvent.VERIFICATION_FAILED_NO_ROLLBACK,
    ): IncidentStatus.ESCALATED,
    (IncidentStatus.ESCALATED, IncidentTransitionEvent.HUMAN_RESOLVED): IncidentStatus.RESOLVED,
    (IncidentStatus.RESOLVED, IncidentTransitionEvent.POST_REVIEW): IncidentStatus.CLOSED,
    (IncidentStatus.FAILED, IncidentTransitionEvent.ESCALATE): IncidentStatus.ESCALATED,
}


class IllegalTransition(IllegalStateError):
    """Raised when `event` is not a legal transition out of `current`."""

    def __init__(self, current: IncidentStatus, event: IncidentTransitionEvent) -> None:
        self.current = current
        self.event = event
        super().__init__(f"illegal transition: {event.value!r} from state {current.value!r}")


def transition(current: IncidentStatus, event: IncidentTransitionEvent) -> IncidentStatus:
    """Compute the next `IncidentStatus`, or raise `IllegalTransition`."""
    try:
        return _TRANSITIONS[(current, event)]
    except KeyError:
        raise IllegalTransition(current, event) from None


def legal_events(current: IncidentStatus) -> frozenset[IncidentTransitionEvent]:
    """Every event that is a legal transition out of `current`. Useful for API validation."""
    return frozenset(event for state, event in _TRANSITIONS if state == current)
