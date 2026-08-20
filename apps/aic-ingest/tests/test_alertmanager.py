from __future__ import annotations

from datetime import UTC, datetime

from aic_common.config import Environment
from aic_ingest.alertmanager import AlertmanagerWebhookPayload, to_alert_events

RECEIVED_AT = datetime(2026, 8, 20, 12, 0, 5, tzinfo=UTC)


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


def test_maps_a_firing_alert_to_an_alert_event() -> None:
    events = to_alert_events(_payload(), environment=Environment.LOCAL, received_at=RECEIVED_AT)

    assert len(events) == 1
    event, partition_key = events[0]
    assert event.alert_fingerprint == "abc123"
    assert event.alertname == "HighLatencyPaymentService"
    assert event.service == "payment-service"
    assert event.severity_label == "critical"
    assert event.environment == Environment.LOCAL
    assert event.starts_at == datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    assert event.received_at == RECEIVED_AT
    # canonical group key: checkout-service depends_on payment-service
    assert partition_key == "checkout-service"


def test_resolved_alerts_are_not_produced() -> None:
    payload = _payload(alerts=[_alert(status="resolved")])
    events = to_alert_events(payload, environment=Environment.LOCAL, received_at=RECEIVED_AT)
    assert events == []


def test_multiple_alerts_in_one_webhook_all_map() -> None:
    other = _alert(
        fingerprint="def456",
        labels={"alertname": "HighErrorRatePaymentService", "service": "payment-service"},
    )
    payload = _payload(alerts=[_alert(), other])

    events = to_alert_events(payload, environment=Environment.LOCAL, received_at=RECEIVED_AT)

    assert {e.alert_fingerprint for e, _ in events} == {"abc123", "def456"}


def test_alert_without_a_service_label_is_skipped() -> None:
    payload = _payload(alerts=[_alert(labels={"alertname": "HighLatencyPaymentService"})])
    events = to_alert_events(payload, environment=Environment.LOCAL, received_at=RECEIVED_AT)
    assert events == []
