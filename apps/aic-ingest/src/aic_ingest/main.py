"""aic-ingest (design doc §1.4 CORRELATE row): receives Alertmanager
webhooks, normalizes each firing alert to an `AlertEvent`, and produces it
to the `alert-events` Kafka topic (ADR 0002). Does not touch Postgres —
that's `aic-correlator`'s job on the consuming side.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from aic_common.clock import Clock, SystemClock
from aic_common.logging import configure_logging, get_logger
from aic_eventbus.kafka import KafkaEventBus, ensure_topic
from aic_eventbus.port import EventBusPort
from fastapi import FastAPI

from aic_ingest.alertmanager import AlertmanagerWebhookPayload, to_alert_events
from aic_ingest.config import IngestSettings

logger = get_logger(__name__)


def create_app(
    settings: IngestSettings | None = None,
    event_bus: EventBusPort | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the app. Tests pass a fake `event_bus` to exercise the
    webhook-to-AlertEvent mapping without a real Kafka broker; the
    real-Kafka path is proven separately in `aic_eventbus`'s own tests and
    in this app's own Kafka integration test."""
    settings = settings or IngestSettings()
    configure_logging(settings.log_level)
    clock = clock or SystemClock()
    owns_bus = event_bus is None
    kafka_bus: KafkaEventBus | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal event_bus, kafka_bus
        if owns_bus:
            kafka_bus = KafkaEventBus(settings.kafka_bootstrap_servers)
            await kafka_bus.start()
            event_bus = kafka_bus
            await ensure_topic(settings.kafka_bootstrap_servers, settings.topic)
        app.state.event_bus = event_bus
        logger.info("aic_ingest.started", topic=settings.topic)
        try:
            yield
        finally:
            if owns_bus and kafka_bus is not None:
                await kafka_bus.stop()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/alertmanager")
    async def receive_alertmanager_webhook(payload: AlertmanagerWebhookPayload) -> dict[str, int]:
        bus: EventBusPort = app.state.event_bus
        assert clock is not None
        events = to_alert_events(payload, environment=settings.environment, received_at=clock.now())
        for event, partition_key in events:
            await bus.publish(settings.topic, partition_key, event.model_dump_json().encode())
            logger.info(
                "aic_ingest.alert_event_published",
                alertname=event.alertname,
                service=event.service,
                alert_fingerprint=event.alert_fingerprint,
                partition_key=partition_key,
            )
        return {"published": len(events)}

    return app


def main() -> None:
    settings = IngestSettings()
    uvicorn.run(create_app(settings=settings), host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
