from datetime import UTC, datetime

import pytest
from aic_common.config import Environment
from aic_contracts.events import AlertEvent
from pydantic import ValidationError


def _event(**overrides: object) -> AlertEvent:
    defaults: dict[str, object] = dict(
        alert_fingerprint="fp1",
        alertname="HighLatencyPaymentService",
        service="payment-service",
        environment=Environment.LOCAL,
        severity_label="critical",
        labels={"alertname": "HighLatencyPaymentService", "service": "payment-service"},
        starts_at=datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC),
        generator_url="http://prometheus:9090/graph",
        received_at=datetime(2026, 8, 20, 12, 0, 1, tzinfo=UTC),
    )
    defaults.update(overrides)
    return AlertEvent.model_validate(defaults)


def test_round_trips_through_json() -> None:
    event = _event()
    restored = AlertEvent.model_validate_json(event.model_dump_json())
    assert restored == event


def test_rejects_naive_starts_at() -> None:
    with pytest.raises(ValidationError):
        _event(starts_at=datetime(2026, 8, 20, 12, 0, 0))


def test_rejects_naive_received_at() -> None:
    with pytest.raises(ValidationError):
        _event(received_at=datetime(2026, 8, 20, 12, 0, 0))


def test_is_frozen() -> None:
    event = _event()
    with pytest.raises(ValidationError):
        event.alertname = "Other"


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _event(unexpected="field")
