"""Settings for aic-correlator.

Runs as a host process, like `apps/toy-ops`: it reads `AIC_DATABASE_URL`
directly via `aic_database.session.DatabaseSettings` (same convention as
the deploy script) and reaches Kafka through the same `localhost`-mapped
NodePort `aic-ingest` produces through — see the design note in
`infra/kind/eventbus/redpanda.yaml`.
"""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from aic_contracts.events import ALERT_EVENTS_TOPIC
from pydantic_settings import SettingsConfigDict


class CorrelatorSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_CORRELATOR_")

    kafka_bootstrap_servers: str = "localhost:30092"
    topic: str = ALERT_EVENTS_TOPIC
    group_id: str = "aic-correlator"
