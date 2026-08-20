"""Parses Alertmanager's webhook payload and maps it to `AlertEvent`s
(design doc §1.8's wire contract), pure and independent of FastAPI so it's
unit-testable against fixture JSON without spinning up the app.
"""

from __future__ import annotations

from datetime import datetime

from aic_common.config import Environment
from aic_contracts.events import AlertEvent
from aic_domain.correlation import DEFAULT_SERVICE_DEPENDENCIES, ServiceDependencyGraph
from pydantic import BaseModel, ConfigDict, Field

_GRAPH = ServiceDependencyGraph.from_pairs(DEFAULT_SERVICE_DEPENDENCIES)


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime = Field(alias="endsAt")
    generator_url: str | None = Field(default=None, alias="generatorURL")
    fingerprint: str


class AlertmanagerWebhookPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str | None = None
    group_key: str | None = Field(default=None, alias="groupKey")
    status: str
    receiver: str | None = None
    alerts: list[AlertmanagerAlert]


def to_alert_events(
    payload: AlertmanagerWebhookPayload,
    *,
    environment: Environment,
    received_at: datetime,
) -> list[tuple[AlertEvent, str]]:
    """Map firing alerts to `(AlertEvent, kafka_partition_key)` pairs.

    Only `status == "firing"` alerts are produced — resolved-notification
    handling (Alertmanager's `send_resolved`) isn't specified anywhere in
    design doc §1.4's CORRELATE row, so it's out of scope for this task
    rather than guessed at.
    """
    results: list[tuple[AlertEvent, str]] = []
    for alert in payload.alerts:
        if alert.status != "firing":
            continue
        service = alert.labels.get("service") or alert.labels.get("app")
        if service is None:
            continue

        event = AlertEvent(
            alert_fingerprint=alert.fingerprint,
            alertname=alert.labels.get("alertname", "unknown"),
            service=service,
            environment=environment,
            severity_label=alert.labels.get("severity"),
            labels=alert.labels,
            starts_at=alert.starts_at,
            generator_url=alert.generator_url,
            source="alertmanager",
            received_at=received_at,
        )
        partition_key = _GRAPH.group_key(service)
        results.append((event, partition_key))
    return results
