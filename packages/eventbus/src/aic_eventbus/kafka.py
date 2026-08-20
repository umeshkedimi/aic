"""aiokafka-backed `EventBusPort`/`EventConsumerPort` (ADR 0002)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka import errors as kafka_errors
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from aic_eventbus.port import ConsumedMessage

DEFAULT_REQUEST_TIMEOUT_MS = 10_000


async def ensure_topic(
    bootstrap_servers: str,
    topic: str,
    *,
    num_partitions: int = 3,
    replication_factor: int = 1,
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
) -> None:
    """Create `topic` if it doesn't already exist. Idempotent — a topic that
    already exists is not an error, just a no-op."""
    admin = AIOKafkaAdminClient(
        bootstrap_servers=bootstrap_servers, request_timeout_ms=request_timeout_ms
    )
    await admin.start()
    try:
        response = await admin.create_topics([NewTopic(topic, num_partitions, replication_factor)])
        for topic_name, error_code, *_rest in response.topic_errors:
            error_cls = kafka_errors.for_code(error_code)
            if error_cls in (kafka_errors.NoError, kafka_errors.TopicAlreadyExistsError):
                continue
            raise error_cls(f"failed to create topic {topic_name!r}: error code {error_code}")
    finally:
        await admin.close()


class KafkaEventBus:
    """Producer side of `EventBusPort`. `acks="all"` + `enable_idempotence`
    per ADR 0002 — a lost or duplicated `AlertEvent` is worse than a slow
    one."""

    def __init__(
        self, bootstrap_servers: str, *, request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS
    ) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            acks="all",
            enable_idempotence=True,
            request_timeout_ms=request_timeout_ms,
        )

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def publish(self, topic: str, key: str, value: bytes) -> None:
        await self._producer.send_and_wait(topic, value=value, key=key.encode())


class KafkaEventConsumer:
    """Consumer side of `EventConsumerPort` (ADR 0002 consumer group
    `aic-correlator`). Manual offset commit — the caller must only call
    `commit()` after the corresponding DB write has actually landed, so a
    crash between consume and commit is safe to reprocess (at-least-once,
    with correctness resting on the caller's own idempotent writes, not on
    Kafka handling it)."""

    def __init__(self, bootstrap_servers: str, topic: str, group_id: str) -> None:
        self._consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def commit(self) -> None:
        await self._consumer.commit()

    def __aiter__(self) -> AsyncIterator[ConsumedMessage]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[ConsumedMessage]:
        async for record in self._consumer:
            yield ConsumedMessage(
                topic=record.topic,
                partition=record.partition,
                offset=record.offset,
                key=record.key,
                value=record.value,
            )
