"""aic-triage worker (design doc §1.4 TRIAGE row, T6). Polls Postgres for
incidents in `TRIAGING` and runs `aic_agents.triage.triage_incident` on the
oldest one, same host-process pattern as aic-ingest/aic-correlator.

No `SELECT ... FOR UPDATE` here: this runs as a single instance for the
demo, same assumption aic-correlator makes for its own consumer group. If a
second instance is ever needed, add `FOR UPDATE SKIP LOCKED` to
`_find_next_triaging_incident_id` and take the lock in a transaction
separate from the (slow, external) LLM call — never hold a row lock across
an outbound HTTP call.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

import openai
from aic_agents.config import LiteLLMSettings
from aic_agents.litellm_adapter import LiteLLMAdapter
from aic_agents.port import LLMPort
from aic_agents.triage import triage_incident
from aic_common.clock import Clock, SystemClock
from aic_common.logging import configure_logging, get_logger
from aic_database.models import Incident
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from aic_domain.enums import IncidentStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aic_triage.config import TriageSettings

logger = get_logger(__name__)


def _find_next_triaging_incident_id(session_factory: sessionmaker[Session]) -> UUID | None:
    with session_scope(session_factory) as session:
        result: UUID | None = session.execute(
            select(Incident.id)
            .where(Incident.status == IncidentStatus.TRIAGING)
            .order_by(Incident.created_at)
            .limit(1)
        ).scalar_one_or_none()
        return result


async def _poll_once(session_factory: sessionmaker[Session], llm: LLMPort, clock: Clock) -> bool:
    incident_id = _find_next_triaging_incident_id(session_factory)
    if incident_id is None:
        return False

    with session_scope(session_factory) as session:
        incident = await triage_incident(session, incident_id, llm=llm, clock=clock)
        logger.info(
            "aic_triage.incident_triaged",
            incident_id=str(incident.id),
            severity=incident.severity,
            title=incident.title,
        )
    return True


async def run(settings: TriageSettings | None = None) -> None:
    settings = settings or TriageSettings()
    configure_logging(settings.log_level)
    clock = SystemClock()

    db_settings = DatabaseSettings(url=os.environ["AIC_DATABASE_URL"])
    engine = create_database_engine(db_settings)
    session_factory = create_session_factory(engine)

    litellm_settings = LiteLLMSettings(master_key=os.environ["AIC_LITELLM_MASTER_KEY"])
    client = openai.AsyncOpenAI(
        api_key=litellm_settings.master_key, base_url=litellm_settings.base_url
    )
    llm: LLMPort = LiteLLMAdapter(
        client=client,
        session_factory=session_factory,
        timeout_seconds=litellm_settings.timeout_seconds,
    )

    logger.info("aic_triage.started", poll_interval_seconds=settings.poll_interval_seconds)
    try:
        while True:
            processed = await _poll_once(session_factory, llm, clock)
            if not processed:
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
