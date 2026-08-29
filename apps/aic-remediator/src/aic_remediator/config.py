"""Settings for aic-remediator. Runs as a host process, like aic-ingest,
aic-correlator, aic-triage, and aic-investigator (T4/T6/T7) — reads
`AIC_DATABASE_URL` directly and talks to the LiteLLM proxy at its default
host-reachable address (`aic_agents.config.LiteLLMSettings`). The K8s
settings drive `aic_agents.execution.load_executor_credentials` (T10): this
poller mints the *write-scoped* `aic-executor` credential (not the
investigator's read-only one) solely to attach a real dry-run to the
approval card before a human decides — it never itself calls anything
mutating; `apps/aic-executor` (T10) is the only process that does."""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class RemediatorSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_REMEDIATOR_")

    poll_interval_seconds: float = 2.0
    k8s_context: str = "kind-aic-demo"
    k8s_namespace: str = "aic-demo"
    k8s_token_duration: str = "1h"
