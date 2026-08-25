import pytest
from aic_domain.actions import ActionCandidate, PatchConfigParams, RollbackDeploymentParams
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
            config_key="DB_POOL_SIZE",
            from_value="3",
            to_value="20",
        ),
    )
    assert candidate.action_type == ActionType.PATCH_CONFIG


def test_candidate_rejects_mismatched_params() -> None:
    with pytest.raises(ValidationError, match="requires params of type RollbackDeploymentParams"):
        ActionCandidate(
            action_type=ActionType.ROLLBACK_DEPLOYMENT,
            target_resource="payment-service",
            rationale_hint="x",
            params=PatchConfigParams(
                deployment="payment-service",
                config_key="DB_POOL_SIZE",
                from_value="3",
                to_value="20",
            ),
        )


def test_candidates_are_frozen() -> None:
    candidate = ActionCandidate(
        action_type=ActionType.PATCH_CONFIG,
        target_resource="payment-service",
        rationale_hint="x",
        params=PatchConfigParams(
            deployment="payment-service",
            config_key="DB_POOL_SIZE",
            from_value="3",
            to_value="20",
        ),
    )
    with pytest.raises(ValidationError):
        candidate.target_resource = "checkout-service"
