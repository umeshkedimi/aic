"""Every edge in design doc §6's mermaid diagram, transition() must accept.

Every non-edge, it must reject via IllegalTransition. This is what "unit-
tested for every edge including the verification-loop-back and escalation
paths" (T1's done criterion) means concretely.
"""

import pytest
from aic_domain.enums import IncidentStatus, IncidentTransitionEvent
from aic_domain.state_machine import IllegalTransition, legal_events, transition

# (current, event, expected_next) — one row per edge drawn in §6.
VALID_EDGES = [
    (IncidentStatus.OPEN, IncidentTransitionEvent.WORKFLOW_STARTED, IncidentStatus.TRIAGING),
    (
        IncidentStatus.TRIAGING,
        IncidentTransitionEvent.TRIAGE_COMPLETED,
        IncidentStatus.INVESTIGATING,
    ),
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.PROPOSAL_REQUIRES_APPROVAL,
        IncidentStatus.AWAITING_APPROVAL,
    ),
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.ALL_ACTIONS_AUTO_APPROVED,
        IncidentStatus.REMEDIATING,
    ),
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.BUDGET_EXHAUSTED,
        IncidentStatus.ESCALATED,
    ),
    (
        IncidentStatus.INVESTIGATING,
        IncidentTransitionEvent.HUMAN_TAKEOVER,
        IncidentStatus.ESCALATED,
    ),
    (
        IncidentStatus.AWAITING_APPROVAL,
        IncidentTransitionEvent.QUORUM_MET,
        IncidentStatus.REMEDIATING,
    ),
    (
        IncidentStatus.AWAITING_APPROVAL,
        IncidentTransitionEvent.REJECTED,
        IncidentStatus.ESCALATED,
    ),
    (
        IncidentStatus.AWAITING_APPROVAL,
        IncidentTransitionEvent.EXPIRED,
        IncidentStatus.ESCALATED,
    ),
    (
        IncidentStatus.REMEDIATING,
        IncidentTransitionEvent.ACTIONS_EXECUTED,
        IncidentStatus.VERIFYING,
    ),
    (
        IncidentStatus.REMEDIATING,
        IncidentTransitionEvent.FATAL_EXECUTION_ERROR,
        IncidentStatus.FAILED,
    ),
    (IncidentStatus.VERIFYING, IncidentTransitionEvent.SOAK_PASSED, IncidentStatus.RESOLVED),
    (
        IncidentStatus.VERIFYING,
        IncidentTransitionEvent.VERIFICATION_FAILED,
        IncidentStatus.INVESTIGATING,
    ),
    (
        IncidentStatus.VERIFYING,
        IncidentTransitionEvent.VERIFICATION_FAILED_NO_ROLLBACK,
        IncidentStatus.ESCALATED,
    ),
    (
        IncidentStatus.ESCALATED,
        IncidentTransitionEvent.HUMAN_RESOLVED,
        IncidentStatus.RESOLVED,
    ),
    (IncidentStatus.RESOLVED, IncidentTransitionEvent.POST_REVIEW, IncidentStatus.CLOSED),
    (IncidentStatus.FAILED, IncidentTransitionEvent.ESCALATE, IncidentStatus.ESCALATED),
]


@pytest.mark.parametrize(
    "current,event,expected", VALID_EDGES, ids=[f"{c}-{e}" for c, e, _ in VALID_EDGES]
)
def test_valid_edge(
    current: IncidentStatus, event: IncidentTransitionEvent, expected: IncidentStatus
) -> None:
    assert transition(current, event) == expected


def test_every_diagram_edge_is_covered_exactly_once() -> None:
    """Guards against the test list itself silently drifting from the diagram."""
    assert len(VALID_EDGES) == 17
    assert len({(c, e) for c, e, _ in VALID_EDGES}) == 17


def test_verification_loop_back_returns_to_investigating() -> None:
    assert (
        transition(IncidentStatus.VERIFYING, IncidentTransitionEvent.VERIFICATION_FAILED)
        == IncidentStatus.INVESTIGATING
    )


@pytest.mark.parametrize(
    "current,event",
    [
        (IncidentStatus.INVESTIGATING, IncidentTransitionEvent.REJECTED),
        (IncidentStatus.AWAITING_APPROVAL, IncidentTransitionEvent.BUDGET_EXHAUSTED),
    ],
    ids=["rejected-does-not-apply-outside-awaiting-approval", "wrong-escalation-trigger"],
)
def test_escalation_events_only_fire_from_their_own_state(
    current: IncidentStatus, event: IncidentTransitionEvent
) -> None:
    with pytest.raises(IllegalTransition):
        transition(current, event)


@pytest.mark.parametrize(
    "current,event",
    [
        (IncidentStatus.OPEN, IncidentTransitionEvent.TRIAGE_COMPLETED),
        (IncidentStatus.CLOSED, IncidentTransitionEvent.WORKFLOW_STARTED),
        (IncidentStatus.RESOLVED, IncidentTransitionEvent.WORKFLOW_STARTED),
        (IncidentStatus.OPEN, IncidentTransitionEvent.SOAK_PASSED),
    ],
)
def test_illegal_transition_raises_with_details(
    current: IncidentStatus, event: IncidentTransitionEvent
) -> None:
    with pytest.raises(IllegalTransition) as exc_info:
        transition(current, event)
    assert exc_info.value.current == current
    assert exc_info.value.event == event


def test_terminal_states_have_no_outgoing_edges_except_closed_and_current_design() -> None:
    # `closed` is the only true terminal state in §6 — everything else has at
    # least one way out (even `failed` escalates).
    assert legal_events(IncidentStatus.CLOSED) == frozenset()


def test_legal_events_matches_diagram_for_investigating() -> None:
    assert legal_events(IncidentStatus.INVESTIGATING) == {
        IncidentTransitionEvent.PROPOSAL_REQUIRES_APPROVAL,
        IncidentTransitionEvent.ALL_ACTIONS_AUTO_APPROVED,
        IncidentTransitionEvent.BUDGET_EXHAUSTED,
        IncidentTransitionEvent.HUMAN_TAKEOVER,
    }


def test_legal_events_matches_diagram_for_verifying() -> None:
    assert legal_events(IncidentStatus.VERIFYING) == {
        IncidentTransitionEvent.SOAK_PASSED,
        IncidentTransitionEvent.VERIFICATION_FAILED,
        IncidentTransitionEvent.VERIFICATION_FAILED_NO_ROLLBACK,
    }
