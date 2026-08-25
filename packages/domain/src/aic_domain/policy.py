"""In-process, versioned policy rule table (design doc §1.10).

Keyed on `action_type x environment x blast_radius -> PolicyEffect`, exactly
per §1.10's predicate shape. Rules are literal, versioned Python data here,
not Postgres rows: `aic_domain.triage.assess_severity` already established
this project's convention for a closed, unit-tested decision table (a
deterministic lookup is data, not I/O), and §1.10's own rationale for
*not* reaching for OPA yet ("one small, well-understood decision space for
v1") applies just as well to not reaching for a DB-backed rule table yet.
What the design doc's durability requirement actually is — recording
*which* rule+version decided a given action, so the table can change later
without losing the historical record of what decided a past decision — is
what `PolicyDecision.rule_id`/`rule_version` (already Postgres-backed, §5)
exists for; it does not require the rule table itself to live in the DB.

Every `(action_type, environment, blast_radius)` combination not
explicitly listed here is denied by default: a new action type or a new
environment must be a deliberate, reviewed addition to this table before it
can ever auto-run or even reach a human for approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aic_common.config import Environment

from aic_domain.enums import ActionType, BlastRadius, PolicyEffect

_RULE_TABLE_VERSION = 1


@dataclass(frozen=True, slots=True)
class PolicyRule:
    rule_id: str
    version: int
    effect: PolicyEffect
    quorum: int | None = None
    required_roles: tuple[str, ...] = field(default_factory=tuple)


_DEFAULT_FORBID_RULE = PolicyRule(
    rule_id="default_forbid_unmatched",
    version=_RULE_TABLE_VERSION,
    effect=PolicyEffect.FORBID,
)

_RULES: dict[tuple[ActionType, Environment, BlastRadius], PolicyRule] = {
    (ActionType.ROLLBACK_DEPLOYMENT, Environment.PROD, BlastRadius.SINGLE_SERVICE): PolicyRule(
        rule_id="rollback_deployment_prod_single_service",
        version=_RULE_TABLE_VERSION,
        effect=PolicyEffect.REQUIRE_APPROVAL,
        quorum=1,
        required_roles=("sre",),
    ),
    (ActionType.ROLLBACK_DEPLOYMENT, Environment.STAGING, BlastRadius.SINGLE_SERVICE): PolicyRule(
        rule_id="rollback_deployment_staging_single_service",
        version=_RULE_TABLE_VERSION,
        effect=PolicyEffect.AUTO_APPROVE,
    ),
    (ActionType.PATCH_CONFIG, Environment.PROD, BlastRadius.SINGLE_SERVICE): PolicyRule(
        rule_id="patch_config_prod_single_service",
        version=_RULE_TABLE_VERSION,
        effect=PolicyEffect.REQUIRE_APPROVAL,
        quorum=1,
        required_roles=("sre",),
    ),
    (ActionType.PATCH_CONFIG, Environment.STAGING, BlastRadius.SINGLE_SERVICE): PolicyRule(
        rule_id="patch_config_staging_single_service",
        version=_RULE_TABLE_VERSION,
        effect=PolicyEffect.AUTO_APPROVE,
    ),
}


def classify_blast_radius(action_type: ActionType) -> BlastRadius:
    """Both catalog action types (`RollbackDeployment`, `PatchConfig`)
    target exactly one Deployment, so this always returns `SINGLE_SERVICE`
    today. Kept as a real function — not a constant inlined at the call
    site — so a future action type that fans out across services has one
    place to teach the classifier about it, per the same action_type."""
    del action_type  # every current catalog entry is single-service
    return BlastRadius.SINGLE_SERVICE


def evaluate_policy(
    *, action_type: ActionType, environment: Environment, blast_radius: BlastRadius
) -> PolicyRule:
    """Pure lookup — deny by default (see module docstring)."""
    return _RULES.get((action_type, environment, blast_radius), _DEFAULT_FORBID_RULE)
