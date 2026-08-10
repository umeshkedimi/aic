import pytest

from aic_domain.incidents.errors import IllegalTransition
from aic_domain.incidents.state import IncidentStatus, IncidentTransitionEvent, transition


def test_happy_path_open_to_resolved() -> None:
    status = IncidentStatus.OPEN
    status = transition(status, IncidentTransitionEvent.WORKFLOW_STARTED)
    assert status == IncidentStatus.TRIAGING
    status = transition(status, IncidentTransitionEvent.TRIAGE_COMPLETED)
    assert status == IncidentStatus.INVESTIGATING
    status = transition(status, IncidentTransitionEvent.ALL_ACTIONS_AUTO_APPROVED)
    assert status == IncidentStatus.REMEDIATING
    status = transition(status, IncidentTransitionEvent.ACTIONS_EXECUTED)
    assert status == IncidentStatus.VERIFYING
    status = transition(status, IncidentTransitionEvent.SOAK_PASSED)
    assert status == IncidentStatus.RESOLVED
    status = transition(status, IncidentTransitionEvent.POST_REVIEW)
    assert status == IncidentStatus.CLOSED


def test_verification_failure_loops_back_to_remediating() -> None:
    status = transition(IncidentStatus.VERIFYING, IncidentTransitionEvent.ROLLBACK)
    assert status == IncidentStatus.REMEDIATING


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(IllegalTransition):
        transition(IncidentStatus.OPEN, IncidentTransitionEvent.SOAK_PASSED)


def test_closed_is_terminal() -> None:
    with pytest.raises(IllegalTransition):
        transition(IncidentStatus.CLOSED, IncidentTransitionEvent.WORKFLOW_STARTED)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (IncidentTransitionEvent.PROPOSAL_REQUIRES_APPROVAL, IncidentStatus.AWAITING_APPROVAL),
        (IncidentTransitionEvent.BUDGET_EXHAUSTED, IncidentStatus.ESCALATED),
        (IncidentTransitionEvent.HUMAN_TAKEOVER, IncidentStatus.ESCALATED),
    ],
)
def test_investigating_branches(
    event: IncidentTransitionEvent, expected: IncidentStatus
) -> None:
    assert transition(IncidentStatus.INVESTIGATING, event) == expected
