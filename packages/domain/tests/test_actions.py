import pytest
from aic_domain.actions import (
    ActionCandidate,
    ConfigChange,
    PatchConfigParams,
    RollbackDeploymentParams,
)
from aic_domain.enums import ActionType
from pydantic import ValidationError


def test_rollback_candidate_accepts_matching_params() -> None:
    candidate = ActionCandidate(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        target_resource="payment-service",
        rationale_hint="bad deploy v42 introduced DB_POOL_SIZE=3",
        params=RollbackDeploymentParams(
            deployment="payment-service", from_version="v42", to_version="v41"
        ),
    )
    assert candidate.action_type == ActionType.ROLLBACK_DEPLOYMENT


def test_patch_config_candidate_accepts_matching_params() -> None:
    candidate = ActionCandidate(
        action_type=ActionType.PATCH_CONFIG,
        target_resource="payment-service",
        rationale_hint="revert DB_POOL_SIZE to its prior value",
        params=PatchConfigParams(
            deployment="payment-service",
            changes=[ConfigChange(key="DB_POOL_SIZE", from_value="3", to_value="20")],
        ),
    )
    assert candidate.action_type == ActionType.PATCH_CONFIG


def test_patch_config_can_bundle_multiple_config_changes_in_one_candidate() -> None:
    candidate = ActionCandidate(
        action_type=ActionType.PATCH_CONFIG,
        target_resource="payment-service",
        rationale_hint="revert two knobs the bad deploy changed",
        params=PatchConfigParams(
            deployment="payment-service",
            changes=[
                ConfigChange(key="DB_POOL_SIZE", from_value="3", to_value="20"),
                ConfigChange(key="DB_TIMEOUT_MS", from_value="500", to_value="2000"),
            ],
        ),
    )
    assert len(candidate.params.changes) == 2  # type: ignore[union-attr]


def test_patch_config_requires_at_least_one_change() -> None:
    with pytest.raises(ValidationError):
        PatchConfigParams(deployment="payment-service", changes=[])


def test_candidate_rejects_mismatched_params() -> None:
    with pytest.raises(ValidationError, match="requires params of type RollbackDeploymentParams"):
        ActionCandidate(
            action_type=ActionType.ROLLBACK_DEPLOYMENT,
            target_resource="payment-service",
            rationale_hint="x",
            params=PatchConfigParams(
                deployment="payment-service",
                changes=[ConfigChange(key="DB_POOL_SIZE", from_value="3", to_value="20")],
            ),
        )


def test_candidates_are_frozen() -> None:
    candidate = ActionCandidate(
        action_type=ActionType.PATCH_CONFIG,
        target_resource="payment-service",
        rationale_hint="x",
        params=PatchConfigParams(
            deployment="payment-service",
            changes=[ConfigChange(key="DB_POOL_SIZE", from_value="3", to_value="20")],
        ),
    )
    with pytest.raises(ValidationError):
        candidate.target_resource = "checkout-service"
