"""aic-investigator worker (design doc §1.6/§1.7, T7). Polls Postgres for
incidents in `INVESTIGATING` that don't have an `RCA` row yet and runs the
investigation graph (`aic_agents.graphs.investigation.run_investigation`)
on the oldest one. Same host-process pattern as aic-ingest/aic-correlator/
aic-triage (T4/T6).

"Already has an RCA" is what marks an incident done here, not a status
transition — finishing investigation doesn't move `Incident.status` by
itself (§6); PLAN REMEDIATION/APPLY POLICY (T8) decide where it goes next.

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
from aic_agents.litellm_adapter import LiteLLMAdapter
from aic_agents.port import LLMPort
from aic_agents.tools.k8s import load_investigator_credentials
from aic_agents.tools.loki import LokiSettings
from aic_agents.tools.prometheus import PrometheusSettings
from aic_agents.tools.registry import ToolRegistry, build_registry
from aic_common.clock import Clock, SystemClock
from aic_common.logging import configure_logging, get_logger
from aic_database.models import RCA, Incident
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from aic_domain.enums import IncidentStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aic_investigator.config import InvestigatorSettings

logger = get_logger(__name__)


def _find_next_investigating_incident_id(session_factory: sessionmaker[Session]) -> UUID | None:
    with session_scope(session_factory) as session:
        already_investigated = select(RCA.incident_id)
        result: UUID | None = session.execute(
            select(Incident.id)
            .where(Incident.status == IncidentStatus.INVESTIGATING)
            .where(Incident.id.not_in(already_investigated))
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
    registry = build_registry(
        prometheus_settings=PrometheusSettings(),
        loki_settings=LokiSettings(),
        k8s_credentials=k8s_credentials,
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
