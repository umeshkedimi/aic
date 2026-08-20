"""End-to-end integration test against a real Kafka broker and a real,
migrated Postgres — this is the literal T4 "Done when" proof: the real T3
fault produces three alerts (`HighLatencyPaymentService`,
`HighErrorRatePaymentService`, `DBPoolExhaustionPaymentService`) that must
correlate into exactly one `Incident`, and replaying one of them must not
create a duplicate `IncidentSignal`. `aic-ingest`'s own tests already prove
the webhook-to-Kafka mapping is correct; this test proves the other half of
ADR 0002's contract — that `aic-correlator` consuming from the real wire
protocol behaves correctly, including under at-least-once redelivery.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from aic_common.clock import SystemClock
from aic_common.config import Environment
from aic_contracts.events import AlertEvent
from aic_correlator.correlate import process_alert_event
from aic_database.models import IncidentSignal
from aic_domain.correlation import DEFAULT_SERVICE_DEPENDENCIES, ServiceDependencyGraph
from aic_eventbus.kafka import KafkaEventBus, KafkaEventConsumer, ensure_topic
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

GRAPH = ServiceDependencyGraph.from_pairs(DEFAULT_SERVICE_DEPENDENCIES)
T0 = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)

ALERTNAMES = (
    "HighLatencyPaymentService",
    "HighErrorRatePaymentService",
    "DBPoolExhaustionPaymentService",
)


def _event(alertname: str, fingerprint: str) -> AlertEvent:
    return AlertEvent(
        alert_fingerprint=fingerprint,
        alertname=alertname,
        service="payment-service",
        environment=Environment.LOCAL,
        severity_label="critical",
        labels={"alertname": alertname, "service": "payment-service", "severity": "critical"},
        starts_at=T0,
        generator_url="http://prometheus:9090/graph",
        received_at=T0,
    )


async def _consume_n(
    consumer: KafkaEventConsumer, session_factory: sessionmaker[Session], n: int
) -> None:
    clock = SystemClock()
    consumed = 0
    async for message in consumer:
        event = AlertEvent.model_validate_json(message.value)
        with session_factory() as session:
            process_alert_event(session, event, graph=GRAPH, clock=clock)
            session.commit()
        await consumer.commit()
        consumed += 1
        if consumed >= n:
            return


def _signals_for_this_run(
    session_factory: sessionmaker[Session], run_id: str
) -> list[IncidentSignal]:
    with session_factory() as session:
        return list(
            session.execute(
                select(IncidentSignal).where(
                    IncidentSignal.alert_fingerprint.startswith(f"fp-{run_id}-")
                )
            )
            .scalars()
            .all()
        )


async def test_three_real_alerts_correlate_into_exactly_one_incident_and_replay_is_idempotent(
    kafka_bootstrap_server: str, session_factory: sessionmaker[Session]
) -> None:
    run_id = uuid.uuid4().hex[:8]
    topic = f"alert-events-{run_id}"
    await ensure_topic(kafka_bootstrap_server, topic, num_partitions=3)

    bus = KafkaEventBus(kafka_bootstrap_server)
    await bus.start()
    try:
        for alertname in ALERTNAMES:
            event = _event(alertname, fingerprint=f"fp-{run_id}-{alertname}")
            key = GRAPH.group_key(event.service)
            await bus.publish(topic, key, event.model_dump_json().encode())
    finally:
        await bus.stop()

    consumer = KafkaEventConsumer(kafka_bootstrap_server, topic, group_id="test-correlator")
    await consumer.start()
    try:
        await _consume_n(consumer, session_factory, n=3)
    finally:
        await consumer.stop()

    signals = _signals_for_this_run(session_factory, run_id)
    assert len(signals) == 3
    assert {s.alertname for s in signals} == set(ALERTNAMES)
    assert len({s.incident_id for s in signals}) == 1

    # Replay: the same webhook (same alert_fingerprint) landing on the topic
    # again — real at-least-once redelivery, not a claim of it.
    bus = KafkaEventBus(kafka_bootstrap_server)
    await bus.start()
    try:
        replayed = _event(ALERTNAMES[0], fingerprint=f"fp-{run_id}-{ALERTNAMES[0]}")
        await bus.publish(
            topic, GRAPH.group_key(replayed.service), replayed.model_dump_json().encode()
        )
    finally:
        await bus.stop()

    consumer = KafkaEventConsumer(kafka_bootstrap_server, topic, group_id="test-correlator")
    await consumer.start()
    try:
        await _consume_n(consumer, session_factory, n=1)
    finally:
        await consumer.stop()

    signals = _signals_for_this_run(session_factory, run_id)
    assert len(signals) == 3  # replay did not create a fourth signal
    assert len({s.incident_id for s in signals}) == 1
