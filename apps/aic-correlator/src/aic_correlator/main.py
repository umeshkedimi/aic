"""aic-correlator worker (design doc §1.4 CORRELATE row, ADR 0002 consumer
group `aic-correlator`). A plain asyncio consume loop, not a FastAPI app —
nothing here serves HTTP; receiving Alertmanager's webhook is aic-ingest's
job.
"""

from __future__ import annotations

import asyncio
import os

from aic_common.clock import SystemClock
from aic_common.logging import configure_logging, get_logger
from aic_contracts.events import AlertEvent
from aic_database.models import ServiceDependency as ServiceDependencyRow
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from aic_domain.correlation import ServiceDependencyGraph
from aic_domain.models import ServiceDependency
from aic_eventbus.kafka import KafkaEventConsumer, ensure_topic
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aic_correlator.config import CorrelatorSettings
from aic_correlator.correlate import process_alert_event

logger = get_logger(__name__)


def load_dependency_graph(session_factory: sessionmaker[Session]) -> ServiceDependencyGraph:
    """Read the live `service_dependency` table — seeded by `make demo-seed`
    (`apps/toy-ops/src/aic_toy_ops/seed_service_dependencies.py`) — rather
    than importing the constant directly, proving the DB-backed config path
    the design doc describes ("the correlator holds a static
    ServiceDependency table") actually works."""
    with session_scope(session_factory) as session:
        rows = session.execute(select(ServiceDependencyRow)).scalars().all()
        edges = [ServiceDependency(service=row.service, depends_on=row.depends_on) for row in rows]
    return ServiceDependencyGraph(edges)


async def run(settings: CorrelatorSettings | None = None) -> None:
    settings = settings or CorrelatorSettings()
    configure_logging(settings.log_level)
    clock = SystemClock()

    db_settings = DatabaseSettings(url=os.environ["AIC_DATABASE_URL"])
    engine = create_database_engine(db_settings)
    session_factory = create_session_factory(engine)
    graph = load_dependency_graph(session_factory)

    await ensure_topic(settings.kafka_bootstrap_servers, settings.topic)
    consumer = KafkaEventConsumer(
        settings.kafka_bootstrap_servers, settings.topic, settings.group_id
    )
    await consumer.start()
    logger.info("aic_correlator.started", group_id=settings.group_id, topic=settings.topic)
    try:
        async for message in consumer:
            event = AlertEvent.model_validate_json(message.value)
            with session_scope(session_factory) as session:
                incident_id = process_alert_event(session, event, graph=graph, clock=clock)
            logger.info(
                "aic_correlator.alert_event_processed",
                incident_id=str(incident_id),
                alert_fingerprint=event.alert_fingerprint,
            )
            await consumer.commit()
    finally:
        await consumer.stop()
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
