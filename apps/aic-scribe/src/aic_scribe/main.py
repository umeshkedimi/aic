"""aic-scribe worker (design doc §1.4 LEARN row, §1.13, T12). Polls
Postgres for incidents in `RESOLVED` and runs
`aic_agents.scribe.draft_postmortem` on the oldest one, same host-process
pattern as aic-triage/aic-investigator/aic-remediator.

No extra "already handled" exclusion is needed in the selection query
(unlike T7's investigator, which needed one after T11's verification
loop-back) — `draft_postmortem` itself moves `Incident.status` off
`RESOLVED` (to `CLOSED`, via `POST_REVIEW`), so the existing status filter
alone prevents re-picking an already-scribed incident on the next poll.
Same reasoning T8's remediator poller used.

No `SELECT ... FOR UPDATE` here, same single-instance assumption every
other poller in this codebase makes today.

Holds no K8s credential — like T11's aic-verifier, this stage only reads/
writes Postgres, calls the LiteLLM proxy, and calls Qdrant.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

import openai
from aic_agents.config import LiteLLMSettings
from aic_agents.knowledge_store import QdrantSettings, warm_up_embedder
from aic_agents.litellm_adapter import LiteLLMAdapter
from aic_agents.port import LLMPort
from aic_agents.scribe import draft_postmortem
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
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aic_scribe.config import ScribeSettings

logger = get_logger(__name__)


def _find_next_resolved_incident_id(session_factory: sessionmaker[Session]) -> UUID | None:
    with session_scope(session_factory) as session:
        result: UUID | None = session.execute(
            select(Incident.id)
            .where(Incident.status == IncidentStatus.RESOLVED)
            .order_by(Incident.resolved_at)
            .limit(1)
        ).scalar_one_or_none()
        return result


async def _poll_once(
    session_factory: sessionmaker[Session],
    llm: LLMPort,
    clock: Clock,
    qdrant_client: AsyncQdrantClient,
    qdrant_settings: QdrantSettings,
) -> bool:
    incident_id = _find_next_resolved_incident_id(session_factory)
    if incident_id is None:
        return False

    with session_scope(session_factory) as session:
        postmortem = await draft_postmortem(
            session,
            incident_id,
            llm=llm,
            clock=clock,
            qdrant_client=qdrant_client,
            qdrant_settings=qdrant_settings,
        )
        logger.info(
            "aic_scribe.postmortem_drafted",
            incident_id=str(incident_id),
            postmortem_id=str(postmortem.id),
            chunks_indexed=len(postmortem.embedding_refs),
        )
    return True


async def run(settings: ScribeSettings | None = None) -> None:
    settings = settings or ScribeSettings()
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

    qdrant_settings = QdrantSettings()
    qdrant_client = AsyncQdrantClient(
        url=qdrant_settings.base_url, timeout=int(qdrant_settings.timeout_seconds)
    )
    # See aic_agents.knowledge_store.warm_up_embedder's own docstring: loads
    # the real embedding model now, off the poll loop, so the first real
    # draft_postmortem call doesn't pay its cold-start cost inline.
    await warm_up_embedder()

    logger.info("aic_scribe.started", poll_interval_seconds=settings.poll_interval_seconds)
    try:
        while True:
            processed = await _poll_once(
                session_factory, llm, clock, qdrant_client, qdrant_settings
            )
            if not processed:
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await qdrant_client.close()
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
