"""Closed remediation action catalog (design doc §1.4 PLAN REMEDIATION row).

An `ActionCandidate` is a pre-typed, schema-validated remediation option —
never free text. `aic_agents.remediation` builds the candidate set
deterministically from RCA evidence; the one LLM call in that stage only
ever *selects* among candidates already built here and writes a rationale
for the choice. It never invents an action type or a param — that
constraint is enforced by types, not by trusting the model's prose.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aic_domain.enums import ActionType


class RollbackDeploymentParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment: str
    from_version: str
    to_version: str


class ConfigChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    from_value: str
    to_value: str


class PatchConfigParams(BaseModel):
    """A single `PatchConfig` action patches every changed key in one
    kubectl call, so all reverted keys live in one candidate's `changes`
    list — never one candidate per key. That keeps the remediation choice
    a true "rollback vs. patch" binary (design doc §1.4's own framing) even
    when a bad deploy touched more than one config key, instead of forcing
    an LLM call to disambiguate between several same-typed candidates that
    a single `action_type` field can't distinguish."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment: str
    changes: list[ConfigChange] = Field(min_length=1)


class ActionCandidate(BaseModel):
    """One pre-typed remediation option offered to the choice LLM call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_type: ActionType
    target_resource: str
    rationale_hint: str = Field(
        description="Deterministic, factual description of why this candidate "
        "exists (e.g. the config diff it would revert) — fed to the LLM as "
        "context, never as something it can override.",
    )
    params: RollbackDeploymentParams | PatchConfigParams

    @model_validator(mode="after")
    def _params_match_action_type(self) -> ActionCandidate:
        expected = {
            ActionType.ROLLBACK_DEPLOYMENT: RollbackDeploymentParams,
            ActionType.PATCH_CONFIG: PatchConfigParams,
        }[self.action_type]
        if not isinstance(self.params, expected):
            raise ValueError(
                f"action_type {self.action_type!r} requires params of type "
                f"{expected.__name__}, got {type(self.params).__name__}"
            )
        return self
