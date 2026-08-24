"""Settings for aic-investigator. Runs as a host process, like
aic-ingest/aic-correlator/aic-triage (T4/T6) — reads `AIC_DATABASE_URL` and
the LiteLLM proxy's default host-reachable address directly. The K8s
settings drive `aic_agents.tools.k8s.load_investigator_credentials`: which
kubeconfig context to mint the `aic-investigator` ServiceAccount's token
from, and how long that token lasts before this process needs a fresh one.
"""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class InvestigatorSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_INVESTIGATOR_")

    poll_interval_seconds: float = 2.0
    k8s_context: str = "kind-aic-demo"
    k8s_namespace: str = "aic-demo"
    k8s_token_duration: str = "1h"
