"""aic-executor worker (design doc §1.4 ACT row, §1.11, ADR 0003, T10).
Polls Postgres for the oldest `Action` with `status == APPROVED` whose
incident is `REMEDIATING`, and runs `aic_agents.execution.execute_action`
on it. Same host-process pattern as aic-ingest/aic-correlator/aic-triage/
aic-investigator/aic-remediator (T4/T6/T7/T8).

This is the one process in the whole system that ever holds the
write-scoped `aic-executor` K8s credential (`aic_agents.execution
.load_executor_credentials`) — the investigation graph (`apps/
aic-investigator`) holds only the read-only `aic-investigator` credential,
and no other process holds either. §1.11's privilege-separation property
("a prompt-injected investigation step has no credential capable of
mutating anything") is a fact about which *process* can reach this module,
not just which module a line of code happens to be in.

`Action.status == APPROVED` alone (not a join on `ExecutionRecord`) is
enough to exclude already-executed actions from re-selection, the same
"the status filter alone" reasoning as T8's remediator poller:
`execute_action` moves `Action.status` to `EXECUTED`/`EXECUTION_FAILED` as
part of the same transaction that writes the `ExecutionRecord`, so a
completed action never matches this query again. `execute_action` is
still independently idempotent on `action_id` (its own docstring) as a
second line of defense against a retried call for the same action.

If `execute_action` raises for a reason other than a real `kubectl`
failure (e.g. `NotFoundError`/`IllegalStateError` — a genuinely
inconsistent DB state), it propagates and crashes this process, matching
every other poller in this codebase. A real `kubectl` failure is *not* an
unhandled exception here: `execute_action` catches `ExecutionError`
itself, persists the failure, and transitions the incident to `FAILED` —
that is this task's designed "human-visible failure, not silent retry"
outcome (design doc §1.14).

No `SELECT ... FOR UPDATE` here, for the same reason as every other
poller: single instance for the demo.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

from aic_agents.execution import (
    ExecutorK8sCredentials,
    Runner,
    execute_action,
    load_executor_credentials,
)
from aic_common.clock import Clock, SystemClock
from aic_common.logging import configure_logging, get_logger
from aic_database.models import Action, Incident, RemediationProposal
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from aic_domain.enums import ActionStatus, IncidentStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aic_executor.config import ExecutorSettings

logger = get_logger(__name__)


def _find_next_approved_action_id(session_factory: sessionmaker[Session]) -> UUID | None:
    with session_scope(session_factory) as session:
        result: UUID | None = session.execute(
            select(Action.id)
            .join(RemediationProposal, RemediationProposal.id == Action.proposal_id)
            .join(Incident, Incident.id == RemediationProposal.incident_id)
            .where(Incident.status == IncidentStatus.REMEDIATING)
            .where(Action.status == ActionStatus.APPROVED.value)
            .order_by(Action.created_at)
            .limit(1)
        ).scalar_one_or_none()
        return result


async def _poll_once(
    session_factory: sessionmaker[Session],
    credentials: ExecutorK8sCredentials,
    clock: Clock,
    kubectl: str,
    runner: Runner | None = None,
) -> bool:
    action_id = _find_next_approved_action_id(session_factory)
    if action_id is None:
        return False

    with session_scope(session_factory) as session:
        if runner is None:
            record = await execute_action(
                session, action_id, credentials=credentials, clock=clock, kubectl=kubectl
            )
        else:
            record = await execute_action(
                session,
                action_id,
                credentials=credentials,
                clock=clock,
                kubectl=kubectl,
                runner=runner,
            )
        logger.info(
            "aic_executor.action_processed",
            action_id=str(action_id),
            execution_record_id=str(record.id),
            status=record.status,
        )
    return True


async def run(settings: ExecutorSettings | None = None) -> None:
    settings = settings or ExecutorSettings()
    configure_logging(settings.log_level)
    clock = SystemClock()

    db_settings = DatabaseSettings(url=os.environ["AIC_DATABASE_URL"])
    engine = create_database_engine(db_settings)
    session_factory = create_session_factory(engine)

    credentials = load_executor_credentials(
        context=settings.k8s_context,
        namespace=settings.k8s_namespace,
        token_duration=settings.k8s_token_duration,
        kubectl=settings.kubectl,
    )

    logger.info("aic_executor.started", poll_interval_seconds=settings.poll_interval_seconds)
    try:
        while True:
            processed = await _poll_once(session_factory, credentials, clock, settings.kubectl)
            if not processed:
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
