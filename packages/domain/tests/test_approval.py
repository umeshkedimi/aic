import pytest
from aic_domain.approval import DEFAULT_APPROVAL_EXPIRY, evaluate_outcome, is_eligible
from aic_domain.enums import ApprovalDecisionType, ApprovalRequestStatus

APPROVE = ApprovalDecisionType.APPROVE
REJECT = ApprovalDecisionType.REJECT


def test_default_approval_expiry_is_a_bounded_positive_duration() -> None:
    assert DEFAULT_APPROVAL_EXPIRY.total_seconds() > 0


@pytest.mark.parametrize(
    ("required_roles", "decider_roles", "expected"),
    [
        (frozenset(), frozenset(), True),
        (frozenset(), frozenset({"sre"}), True),
        (frozenset({"sre"}), frozenset({"sre"}), True),
        (frozenset({"sre"}), frozenset({"sre", "oncall"}), True),
        (frozenset({"sre"}), frozenset({"oncall"}), False),
        (frozenset({"sre"}), frozenset(), False),
    ],
)
def test_is_eligible(
    required_roles: frozenset[str], decider_roles: frozenset[str], expected: bool
) -> None:
    assert is_eligible(required_roles, decider_roles) is expected


def test_evaluate_outcome_is_pending_with_no_decisions() -> None:
    assert evaluate_outcome(quorum=1, decisions=[]) == ApprovalRequestStatus.PENDING


def test_evaluate_outcome_approved_once_quorum_of_approvals_is_met() -> None:
    assert evaluate_outcome(quorum=1, decisions=[APPROVE]) == ApprovalRequestStatus.APPROVED


def test_evaluate_outcome_stays_pending_below_quorum() -> None:
    assert evaluate_outcome(quorum=2, decisions=[APPROVE]) == ApprovalRequestStatus.PENDING


def test_evaluate_outcome_approved_at_exact_quorum_with_more_than_two_deciders() -> None:
    outcome = evaluate_outcome(quorum=2, decisions=[APPROVE, APPROVE])
    assert outcome == ApprovalRequestStatus.APPROVED


def test_evaluate_outcome_a_single_reject_rejects_regardless_of_quorum() -> None:
    assert evaluate_outcome(quorum=1, decisions=[REJECT]) == ApprovalRequestStatus.REJECTED


def test_evaluate_outcome_reject_wins_even_alongside_approvals() -> None:
    assert (
        evaluate_outcome(quorum=2, decisions=[APPROVE, REJECT, APPROVE])
        == ApprovalRequestStatus.REJECTED
    )
