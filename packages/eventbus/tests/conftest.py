from collections.abc import Iterator

import pytest
from testcontainers.community.kafka import KafkaContainer


@pytest.fixture(scope="session")
def kafka_bootstrap_server() -> Iterator[str]:
    """A real, ephemeral Kafka broker (KRaft mode — no ZooKeeper) for the
    Kafka producer/consumer adapter tests. `aic-eventbus`'s whole point is
    wrapping the real Kafka wire protocol; a mocked broker would prove
    nothing about the adapter working."""
    with KafkaContainer("confluentinc/cp-kafka:7.6.0").with_kraft() as container:
        yield container.get_bootstrap_server()
