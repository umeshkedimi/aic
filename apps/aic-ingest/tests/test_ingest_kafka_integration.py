"""Real Kafka integration test — proves the webhook actually reaches a real
broker with the right topic/key/value, not just that the fake bus in
test_ingest_endpoint.py was called correctly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from aic_common.clock import FixedClock
from aic_contracts.events import AlertEvent
from aic_eventbus.kafka import KafkaEventConsumer
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
        annotations={},
        startsAt="2026-08-20T12:00:00Z",
        endsAt="0001-01-01T00:00:00Z",
        generatorURL="http://prometheus:9090/graph",
        fingerprint="abc123",
    )
    defaults.update(overrides)
    return defaults


async def test_webhook_produces_a_real_alert_event_to_kafka(kafka_bootstrap_server: str) -> None:
    topic = f"alert-events-{uuid.uuid4().hex}"
    settings = IngestSettings(kafka_bootstrap_servers=kafka_bootstrap_server, topic=topic)
    app = create_app(
        settings=settings, clock=FixedClock(datetime(2026, 8, 20, 12, 0, 5, tzinfo=UTC))
    )
    body = {"status": "firing", "alerts": [_alert()]}

    with TestClient(app) as client:
        resp = client.post("/webhooks/alertmanager", json=body)
    assert resp.status_code == 200

    consumer = KafkaEventConsumer(kafka_bootstrap_server, topic, group_id="test-group")
    await consumer.start()
    try:
        message = await anext(aiter(consumer))
    finally:
        await consumer.stop()

    assert message.key == b"checkout-service"
    event = AlertEvent.model_validate_json(message.value)
    assert event.alert_fingerprint == "abc123"
    assert event.service == "payment-service"
