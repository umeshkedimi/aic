"""Settings for aic-scribe. Runs as a host process, like aic-triage/
aic-investigator/aic-remediator (T6/T7/T8) — reads `AIC_DATABASE_URL`
directly, talks to the LiteLLM proxy at its default host-reachable address,
and to Qdrant at its default host-reachable address
(`aic_agents.knowledge_store.QdrantSettings`)."""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class ScribeSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_SCRIBE_")

    poll_interval_seconds: float = 2.0
