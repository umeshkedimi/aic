from __future__ import annotations

from datetime import UTC, datetime

from aic_common.clock import FixedClock
from aic_ingest.alertmanager import AlertmanagerWebhookPayload
from aic_ingest.config import IngestSettings
from aic_ingest.main import create_app
from fastapi.testclient import TestClient


def _alert(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = dict(
        status="firing",
        labels={
            "alertname": "HighLatencyPaymentService",
            "severity": "critical",
            "service": "payment-service",
        },
        annotations={"summary": "p99 latency above 1s"},
        startsAt="2026-08-20T12:00:00Z",
        endsAt="0001-01-01T00:00:00Z",
        generatorURL="http://prometheus:9090/graph?g0.expr=...",
        fingerprint="abc123",
    )
    defaults.update(overrides)
    return defaults


def _payload(**overrides: object) -> AlertmanagerWebhookPayload:
    defaults: dict[str, object] = dict(
        version="4",
        groupKey='{}:{alertname="HighLatencyPaymentService"}',
        status="firing",
        receiver="aic-ingest",
        alerts=[_alert()],
    )
    defaults.update(overrides)
    return AlertmanagerWebhookPayload.model_validate(defaults)


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bytes]] = []

    async def publish(self, topic: str, key: str, value: bytes) -> None:
        self.published.append((topic, key, value))


def test_webhook_publishes_one_event_per_firing_alert() -> None:
    bus = FakeEventBus()
    app = create_app(
        settings=IngestSettings(kafka_bootstrap_servers="unused"),
        event_bus=bus,
        clock=FixedClock(datetime(2026, 8, 20, 12, 0, 5, tzinfo=UTC)),
    )
    body = _payload().model_dump(by_alias=True, mode="json")

    with TestClient(app) as client:
        resp = client.post("/webhooks/alertmanager", json=body)

    assert resp.status_code == 200
    assert resp.json() == {"published": 1}
    assert len(bus.published) == 1
    topic, key, value = bus.published[0]
    assert topic == "alert-events"
    assert key == "checkout-service"
    assert b'"alert_fingerprint":"abc123"' in value


def test_resolved_only_webhook_publishes_nothing() -> None:
    bus = FakeEventBus()
    app = create_app(
        settings=IngestSettings(kafka_bootstrap_servers="unused"),
        event_bus=bus,
        clock=FixedClock(datetime(2026, 8, 20, 12, 0, 5, tzinfo=UTC)),
    )
    body = _payload(alerts=[_alert(status="resolved")]).model_dump(by_alias=True, mode="json")

    with TestClient(app) as client:
        resp = client.post("/webhooks/alertmanager", json=body)

    assert resp.status_code == 200
    assert resp.json() == {"published": 0}
    assert bus.published == []


def test_health() -> None:
    app = create_app(
        settings=IngestSettings(kafka_bootstrap_servers="unused"), event_bus=FakeEventBus()
    )
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
