"""Settings for talking to the LiteLLM proxy (`infra/litellm/`, ADR 0004).

The proxy is a plain host-reachable Docker container (`make llm-up`) —
not a kind-cluster service — since only host processes (investigation
graph, triage, scribe) ever call it; see the design note in
`infra/litellm/litellm_config.yaml`.
"""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class LiteLLMSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_LITELLM_")

    base_url: str = "http://localhost:4000"
    master_key: str
    timeout_seconds: float = 30.0
