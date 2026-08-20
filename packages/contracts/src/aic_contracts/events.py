"""Wire contract for the `alert-events` Kafka topic (design doc §1.8, ADR 0002).

Distinct from `aic_domain.models.IncidentSignal` (the persisted domain
record a correlated alert becomes once it's attached to an `Incident`):
`AlertEvent` is the canonical, source-agnostic event `aic-ingest` produces
and `aic-correlator` consumes — the two services' only shared contract, so
it lives in its own package neither app depends on the other's code for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from aic_common.config import Environment
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

ALERT_EVENTS_TOPIC = "alert-events"


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime fields must be timezone-aware")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_timezone)]


class AlertEvent(BaseModel):
    """A single alert, normalized to a source-agnostic shape. Alertmanager
    is the only producer today (`source="alertmanager"`), but nothing here
    bakes that in — §1.17 lists multi-source ingestion as a real future
    direction, and this schema is the seam that would absorb it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_fingerprint: str
    alertname: str
    service: str
    environment: Environment
    severity_label: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    starts_at: UtcDatetime
    generator_url: str | None = None
    source: str = "alertmanager"
    received_at: UtcDatetime
