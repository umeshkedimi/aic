import pytest
from aic_common.config import Environment
from aic_domain.enums import ActionType, BlastRadius, PolicyEffect
from aic_domain.policy import classify_blast_radius, evaluate_policy


@pytest.mark.parametrize("action_type", [ActionType.ROLLBACK_DEPLOYMENT, ActionType.PATCH_CONFIG])
def test_classify_blast_radius_is_single_service_for_every_catalog_entry(
    action_type: ActionType,
) -> None:
    assert classify_blast_radius(action_type) == BlastRadius.SINGLE_SERVICE


@pytest.mark.parametrize(
    ("action_type", "environment", "expected_effect"),
    [
        # Design doc §1.10's own worked example.
        (ActionType.ROLLBACK_DEPLOYMENT, Environment.PROD, PolicyEffect.REQUIRE_APPROVAL),
        (ActionType.ROLLBACK_DEPLOYMENT, Environment.STAGING, PolicyEffect.AUTO_APPROVE),
        (ActionType.PATCH_CONFIG, Environment.PROD, PolicyEffect.REQUIRE_APPROVAL),
        (ActionType.PATCH_CONFIG, Environment.STAGING, PolicyEffect.AUTO_APPROVE),
        # Not in the table -> deny by default.
        (ActionType.ROLLBACK_DEPLOYMENT, Environment.LOCAL, PolicyEffect.FORBID),
        (ActionType.PATCH_CONFIG, Environment.LOCAL, PolicyEffect.FORBID),
    ],
)
def test_evaluate_policy_via_the_real_rule_table(
    action_type: ActionType, environment: Environment, expected_effect: PolicyEffect
) -> None:
    rule = evaluate_policy(
        action_type=action_type,
        environment=environment,
        blast_radius=BlastRadius.SINGLE_SERVICE,
    )
    assert rule.effect == expected_effect


def test_require_approval_rules_carry_a_quorum_and_role() -> None:
    rule = evaluate_policy(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        environment=Environment.PROD,
        blast_radius=BlastRadius.SINGLE_SERVICE,
    )
    assert rule.quorum == 1
    assert rule.required_roles == ("sre",)


def test_auto_approve_rules_carry_no_quorum() -> None:
    rule = evaluate_policy(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        environment=Environment.STAGING,
        blast_radius=BlastRadius.SINGLE_SERVICE,
    )
    assert rule.quorum is None
    assert rule.required_roles == ()


def test_prod_and_staging_rules_for_the_same_action_type_are_distinct_rule_ids() -> None:
    prod_rule = evaluate_policy(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        environment=Environment.PROD,
        blast_radius=BlastRadius.SINGLE_SERVICE,
    )
    staging_rule = evaluate_policy(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        environment=Environment.STAGING,
        blast_radius=BlastRadius.SINGLE_SERVICE,
    )
    assert prod_rule.rule_id != staging_rule.rule_id
    assert prod_rule.effect != staging_rule.effect


def test_unmatched_blast_radius_is_forbidden() -> None:
    rule = evaluate_policy(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        environment=Environment.PROD,
        blast_radius=BlastRadius.MULTI_SERVICE,
    )
    assert rule.effect == PolicyEffect.FORBID
    assert rule.rule_id == "default_forbid_unmatched"
