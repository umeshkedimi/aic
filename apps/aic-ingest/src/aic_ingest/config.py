"""Settings for aic-ingest.

Runs as a host process (not an in-cluster pod) so it can reach the same
`localhost`-mapped Kafka NodePort `apps/toy-ops` already reaches Kafka
through — see the design note in `infra/kind/eventbus/redpanda.yaml`.
Alertmanager (in-cluster) reaches back out to it via `host.docker.internal`
(design doc §1.4 DETECT row: "Each fires a webhook to aic-ingest").
"""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from aic_contracts.events import ALERT_EVENTS_TOPIC
from pydantic_settings import SettingsConfigDict


class IngestSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_INGEST_")

    kafka_bootstrap_servers: str = "localhost:9092"
    topic: str = ALERT_EVENTS_TOPIC
    port: int = 8090
