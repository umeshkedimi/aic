"""Settings for aic-executor. Runs as a host process, like aic-ingest,
aic-correlator, aic-triage, aic-investigator, and aic-remediator
(T4/T6/T7/T8) — reads `AIC_DATABASE_URL` directly. The K8s settings drive
`aic_agents.execution.load_executor_credentials`: which kubeconfig context
to mint the *write-scoped* `aic-executor` ServiceAccount's token from, and
how long that token lasts before this process needs a fresh one. This is
the one process in the whole system that ever holds this credential."""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class ExecutorSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_EXECUTOR_")

    poll_interval_seconds: float = 2.0
    k8s_context: str = "kind-aic-demo"
    k8s_namespace: str = "aic-demo"
    k8s_token_duration: str = "1h"
    kubectl: str = "kubectl"
