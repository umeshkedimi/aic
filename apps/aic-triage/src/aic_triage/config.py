"""Settings for aic-triage. Runs as a host process, like aic-ingest and
aic-correlator (T4) — reads `AIC_DATABASE_URL` directly and talks to the
LiteLLM proxy at its default host-reachable address (`aic_agents.config.
LiteLLMSettings`)."""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class TriageSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_TRIAGE_")

    poll_interval_seconds: float = 2.0
