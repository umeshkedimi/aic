"""Settings for aic-verifier. Runs as a host process, like every other
poller in this codebase (aic-ingest/aic-correlator/aic-triage/
aic-investigator/aic-remediator/aic-executor, T4/T6/T7/T8/T10) — reads
`AIC_DATABASE_URL` directly and talks to Prometheus/Loki at their default
host-reachable addresses (`aic_agents.tools.prometheus.PrometheusSettings`,
`aic_agents.tools.loki.LokiSettings`). Unlike every poller since T7, this
one never touches K8s: verification only reads metrics/logs, never
Deployments/pods, so it holds neither the investigator's nor the
executor's credential."""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class VerifierSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_VERIFIER_")

    poll_interval_seconds: float = 2.0
    soak_seconds: float = 90.0
