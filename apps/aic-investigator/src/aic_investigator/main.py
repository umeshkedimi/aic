"""aic-investigator worker (design doc §1.6/§1.7, T7). Polls Postgres for
incidents in `INVESTIGATING` that need a fresh investigation pass and runs
the investigation graph (`aic_agents.graphs.investigation
.run_investigation`) on the oldest one. Same host-process pattern as
aic-ingest/aic-correlator/aic-triage (T4/T6).

"Needs a fresh pass" is what marks an incident eligible here, not a status
transition — finishing investigation doesn't move `Incident.status` by
itself (§6); PLAN REMEDIATION/APPLY POLICY (T8) decide where it goes next.

**T11 fix to this task's own selection query, not left latent:** the
original rule was "no `RCA` row yet at all", which only accounts for an
incident's *first* time through `INVESTIGATING` (via `triage_completed`).
T11's verification loop-back (`verifying -> investigating` on a failed
soak check, §6) re-enters `INVESTIGATING` on an incident that already has
an `RCA` from its first pass — under the old rule this poller would never
pick it up again, silently stalling the one retry §6 and §1.12 actually
promise. Fixed by counting instead of existence-checking: each time an
incident enters `INVESTIGATING` is either the one `triage_completed`
event or a `verification_failed` event (`aic_agents.verification`), so
`1 + count(verification_failed events)` is exactly how many investigation
passes this incident is entitled to so far. An incident is eligible
whenever its `RCA` count hasn't caught up to that number yet — 0 RCAs on
first entry, 1 RCA (not yet 2) right after a loop-back, etc.

No `SELECT ... FOR UPDATE` here, for the same reason as aic-triage's
poller: single instance for the demo. A second instance would need `FOR
UPDATE SKIP LOCKED` taken in a transaction separate from the graph run
(which makes many slow, external LLM/HTTP/K8s calls) — never hold a row
lock across those.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

import openai
from aic_agents.config import LiteLLMSettings
from aic_agents.graphs.investigation import run_investigation
from aic_agents.knowledge_store import QdrantSettings, warm_up_embedder
from aic_agents.litellm_adapter import LiteLLMAdapter
from aic_agents.port import LLMPort
from aic_agents.tools.k8s import load_investigator_credentials
from aic_agents.tools.loki import LokiSettings
from aic_agents.tools.prometheus import PrometheusSettings
from aic_agents.tools.registry import ToolRegistry, build_registry
from aic_common.clock import Clock, SystemClock
from aic_common.logging import configure_logging, get_logger
from aic_database.models import RCA, Incident, IncidentEvent
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from aic_domain.enums import IncidentStatus, IncidentTransitionEvent
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aic_investigator.config import InvestigatorSettings

logger = get_logger(__name__)


def _find_next_investigating_incident_id(session_factory: sessionmaker[Session]) -> UUID | None:
    with session_scope(session_factory) as session:
        rca_counts = (
            select(RCA.incident_id, func.count(RCA.id).label("rca_count"))
            .group_by(RCA.incident_id)
            .subquery()
        )
        retry_counts = (
            select(
                IncidentEvent.incident_id,
                func.count(IncidentEvent.id).label("retry_count"),
            )
            .where(IncidentEvent.event_type == IncidentTransitionEvent.VERIFICATION_FAILED.value)
            .group_by(IncidentEvent.incident_id)
            .subquery()
        )
        rca_count = func.coalesce(rca_counts.c.rca_count, 0)
        passes_entitled = 1 + func.coalesce(retry_counts.c.retry_count, 0)
        result: UUID | None = session.execute(
            select(Incident.id)
            .outerjoin(rca_counts, rca_counts.c.incident_id == Incident.id)
            .outerjoin(retry_counts, retry_counts.c.incident_id == Incident.id)
            .where(Incident.status == IncidentStatus.INVESTIGATING)
            .where(rca_count < passes_entitled)
            .order_by(Incident.created_at)
            .limit(1)
        ).scalar_one_or_none()
        return result


async def _poll_once(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
    llm: LLMPort,
    clock: Clock,
) -> bool:
    incident_id = _find_next_investigating_incident_id(session_factory)
    if incident_id is None:
        return False

    result = await run_investigation(
        session_factory=session_factory,
        incident_id=incident_id,
        tools=registry.specs,
        llm=llm,
        clock=clock,
    )
    logger.info(
        "aic_investigator.incident_investigated",
        incident_id=str(incident_id),
        rca_id=str(result.rca_id),
        hypothesis_count=len(result.hypotheses),
    )
    return True


async def run(settings: InvestigatorSettings | None = None) -> None:
    settings = settings or InvestigatorSettings()
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

    k8s_credentials = load_investigator_credentials(
        context=settings.k8s_context,
        namespace=settings.k8s_namespace,
        token_duration=settings.k8s_token_duration,
    )
    # Loads the real embedding model now, off the poll loop, so the first
    # real knowledge.search call doesn't pay its cold-start cost inline
    # (aic_agents.knowledge_store.warm_up_embedder's own docstring).
    await warm_up_embedder()
    registry = build_registry(
        prometheus_settings=PrometheusSettings(),
        loki_settings=LokiSettings(),
        k8s_credentials=k8s_credentials,
        qdrant_settings=QdrantSettings(),
        session_factory=session_factory,
    )

    logger.info("aic_investigator.started", poll_interval_seconds=settings.poll_interval_seconds)
    try:
        while True:
            processed = await _poll_once(session_factory, registry, llm, clock)
            if not processed:
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await registry.aclose()
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
