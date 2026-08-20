"""Integration tests against a real Kafka broker (see conftest.py) — the
adapter's whole point is the real Kafka wire protocol, so these prove that,
not a mock of it."""

from __future__ import annotations

import uuid

from aic_eventbus.kafka import KafkaEventBus, KafkaEventConsumer, ensure_topic


def _topic() -> str:
    return f"test-topic-{uuid.uuid4().hex}"


async def test_publish_then_consume_round_trips(kafka_bootstrap_server: str) -> None:
    topic = _topic()
    await ensure_topic(kafka_bootstrap_server, topic, num_partitions=1)

    bus = KafkaEventBus(kafka_bootstrap_server)
    await bus.start()
    try:
        await bus.publish(topic, "key-1", b"hello")
    finally:
        await bus.stop()

    consumer = KafkaEventConsumer(kafka_bootstrap_server, topic, group_id="test-group")
    await consumer.start()
    try:
        message = await anext(aiter(consumer))
    finally:
        await consumer.stop()

    assert message.topic == topic
    assert message.key == b"key-1"
    assert message.value == b"hello"


async def test_ensure_topic_is_idempotent(kafka_bootstrap_server: str) -> None:
    topic = _topic()
    await ensure_topic(kafka_bootstrap_server, topic)
    await ensure_topic(kafka_bootstrap_server, topic)  # must not raise


async def test_same_key_always_routes_to_the_same_partition(kafka_bootstrap_server: str) -> None:
    topic = _topic()
    await ensure_topic(kafka_bootstrap_server, topic, num_partitions=6)

    bus = KafkaEventBus(kafka_bootstrap_server)
    await bus.start()
    try:
        for _ in range(10):
            await bus.publish(topic, "payment-service", b"x")
    finally:
        await bus.stop()

    consumer = KafkaEventConsumer(kafka_bootstrap_server, topic, group_id="test-group")
    await consumer.start()
    try:
        partitions = set()
        it = aiter(consumer)
        for _ in range(10):
            message = await anext(it)
            partitions.add(message.partition)
    finally:
        await consumer.stop()

    assert partitions == {next(iter(partitions))}
