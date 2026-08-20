from collections.abc import Iterator

import pytest
from testcontainers.community.kafka import KafkaContainer


@pytest.fixture(scope="session")
def kafka_bootstrap_server() -> Iterator[str]:
    """A real, ephemeral Kafka broker — see `aic_eventbus`'s own conftest
    for why this isn't mocked."""
    with KafkaContainer("confluentinc/cp-kafka:7.6.0").with_kraft() as container:
        yield container.get_bootstrap_server()
