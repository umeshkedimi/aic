"""aic-remediator worker (design doc §1.4 PLAN REMEDIATION/APPLY POLICY
rows, T8). Polls Postgres for incidents in `INVESTIGATING` that already
have an `RCA` row and runs `aic_agents.remediation.plan_remediation` on the
oldest one. Same host-process pattern as aic-ingest/aic-correlator/
aic-triage/aic-investigator (T4/T6/T7).

Unlike T7's investigator, no extra "already handled" exclusion is needed
here: `plan_remediation` itself moves `Incident.status` away from
`INVESTIGATING` (to `AWAITING_APPROVAL`, `REMEDIATING`, or `ESCALATED`), so
the `status == INVESTIGATING` filter alone keeps an already-planned
incident from being picked up again.

If `plan_remediation` raises (e.g. `NoRemediationCandidateError` — the top
hypothesis genuinely doesn't cite a deployment-correlation), the exception
propagates out of `_poll_once` and crashes this process, same as every
other poller in this codebase today: none of T4/T6/T7's workers catch and
retry a raised domain error either. A real "escalate to a human when
automated planning can't proceed" path is out of this task's scope.

T10 adds a write-scoped `aic-executor` K8s credential at startup, minted
the same way T7's investigator credential is (`aic_agents.k8s_auth`) but
for a different ServiceAccount — `plan_remediation` uses it to attach a
real dry-run to the approval card before a human decides (design doc §1.4
ACT row). This process never itself invokes a mutating `kubectl` command:
only the pre-built, schema-validated candidate `chosen` by (at most) a
closed-choice LLM call is ever handed to the dry run, so no LLM-authored
or externally-sourced content reaches it — `apps/aic-executor` remains the
only process that performs a real, non-dry-run mutation.

No `SELECT ... FOR UPDATE` here, for the same reason as every other
poller: single instance for the demo.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

import openai
from aic_agents.config import LiteLLMSettings
from aic_agents.execution import ExecutorK8sCredentials, load_executor_credentials
from aic_agents.litellm_adapter import LiteLLMAdapter
from aic_agents.port import LLMPort
from aic_agents.remediation import plan_remediation
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

from aic_remediator.config import RemediatorSettings

logger = get_logger(__name__)


def _find_next_investigated_incident_id(session_factory: sessionmaker[Session]) -> UUID | None:
    with session_scope(session_factory) as session:
        has_rca = select(RCA.incident_id)
        result: UUID | None = session.execute(
            select(Incident.id)
            .where(Incident.status == IncidentStatus.INVESTIGATING)
            .where(Incident.id.in_(has_rca))
            .order_by(Incident.created_at)
            .limit(1)
        ).scalar_one_or_none()
        return result


async def _poll_once(
    session_factory: sessionmaker[Session],
    llm: LLMPort,
    clock: Clock,
    executor_credentials: ExecutorK8sCredentials | None = None,
) -> bool:
    incident_id = _find_next_investigated_incident_id(session_factory)
    if incident_id is None:
        return False

    with session_scope(session_factory) as session:
        action = await plan_remediation(
            session,
            incident_id,
            llm=llm,
            clock=clock,
            executor_credentials=executor_credentials,
        )
        logger.info(
            "aic_remediator.remediation_planned",
            incident_id=str(incident_id),
            action_type=action.action_type,
            policy_decision=action.policy_decision,
        )
    return True


async def run(settings: RemediatorSettings | None = None) -> None:
    settings = settings or RemediatorSettings()
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

    executor_credentials = load_executor_credentials(
        context=settings.k8s_context,
        namespace=settings.k8s_namespace,
        token_duration=settings.k8s_token_duration,
    )

    logger.info("aic_remediator.started", poll_interval_seconds=settings.poll_interval_seconds)
    try:
        while True:
            processed = await _poll_once(session_factory, llm, clock, executor_credentials)
            if not processed:
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
