"""Settings for aic-approval-api.

Design doc §1.10 says the v1 delivery surface is "an authenticated API
endpoint" without specifying the auth mechanism. This project has no
broader identity provider anywhere else, so the mechanism here is
deliberately minimal: a static Bearer-token -> identity map, configured as
one JSON env var. Good enough to prove the real trust properties the
design doc actually cares about (who decided, what role they held,
immutable audit of that) without inventing an OAuth/SSO integration this
milestone doesn't need. A real deployment would swap this for whatever
identity provider it already has — the `decider_id`/`roles` shape handed
to `aic_agents.approval.record_decision` doesn't change either way.
"""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict


class DeciderIdentity(BaseModel):
    decider_id: str
    roles: list[str]


class ApprovalApiSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_APPROVAL_API_")

    port: int = 8091
    # Bearer token -> identity, e.g.:
    #   AIC_APPROVAL_API_IDENTITIES='{"tok_alice":{"decider_id":"alice","roles":["sre"]}}'
    identities: dict[str, DeciderIdentity] = Field(default_factory=dict)
