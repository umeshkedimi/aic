"""aic-verifier worker (design doc §1.4 VERIFY/RESOLVE rows, §1.12, T11).
Polls Postgres for the oldest `Incident` with `status == VERIFYING` and
runs `aic_agents.verification.verify_incident` on it. Same host-process
pattern as every other poller in this codebase (T4/T6/T7/T8/T10).

Unlike T8's remediator or T10's executor pollers, no extra "already
handled" exclusion query is needed: `Incident.status == VERIFYING` alone
is enough, because `verify_incident` itself always moves the incident's
status away from `VERIFYING` (to `RESOLVED`, `INVESTIGATING`, or
`ESCALATED`) as part of the same short transaction that persists the
`VerificationRecord` — the status filter alone keeps a just-verified
incident from being re-selected, same reasoning as T8's remediator note.
`verify_incident` is still independently idempotent on `execution_id`
(its own module docstring) as a second line of defense against a retried
call for the same execution.

If `verify_incident` raises for a reason other than the soak/checks
themselves (e.g. `NotFoundError`/`IllegalStateError` — a genuinely
inconsistent DB state), it propagates and crashes this process, matching
every other poller in this codebase.

No `SELECT ... FOR UPDATE` here, for the same reason as every other
poller: single instance for the demo. The 90-second soak wait happens
with no DB session open (`aic_agents.verification`'s own docstring), so a
second instance would additionally need to coordinate around that long
external wait, not just a lock — out of scope for this demo's
single-poller model.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx
from aic_agents.tools.loki import LokiSettings
from aic_agents.tools.prometheus import PrometheusSettings
from aic_agents.verification import verify_incident
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

from aic_verifier.config import VerifierSettings

logger = get_logger(__name__)


def _find_next_verifying_incident_id(session_factory: sessionmaker[Session]) -> UUID | None:
    with session_scope(session_factory) as session:
        result: UUID | None = session.execute(
            select(Incident.id)
            .where(Incident.status == IncidentStatus.VERIFYING)
            .order_by(Incident.created_at)
            .limit(1)
        ).scalar_one_or_none()
        return result


async def _poll_once(
    session_factory: sessionmaker[Session],
    prometheus_client: httpx.AsyncClient,
    loki_client: httpx.AsyncClient,
    clock: Clock,
    soak_seconds: float,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> bool:
    incident_id = _find_next_verifying_incident_id(session_factory)
    if incident_id is None:
        return False

    if sleep is None:
        record = await verify_incident(
            session_factory=session_factory,
            incident_id=incident_id,
            prometheus_client=prometheus_client,
            loki_client=loki_client,
            clock=clock,
            soak_seconds=soak_seconds,
        )
    else:
        record = await verify_incident(
            session_factory=session_factory,
            incident_id=incident_id,
            prometheus_client=prometheus_client,
            loki_client=loki_client,
            clock=clock,
            soak_seconds=soak_seconds,
            sleep=sleep,
        )
    logger.info(
        "aic_verifier.incident_verified",
        incident_id=str(incident_id),
        verification_record_id=str(record.id),
        passed=record.passed,
    )
    return True


async def run(settings: VerifierSettings | None = None) -> None:
    settings = settings or VerifierSettings()
    configure_logging(settings.log_level)
    clock = SystemClock()

    db_settings = DatabaseSettings(url=os.environ["AIC_DATABASE_URL"])
    engine = create_database_engine(db_settings)
    session_factory = create_session_factory(engine)

    prometheus_client = httpx.AsyncClient(base_url=PrometheusSettings().base_url)
    loki_client = httpx.AsyncClient(base_url=LokiSettings().base_url)

    logger.info("aic_verifier.started", poll_interval_seconds=settings.poll_interval_seconds)
    try:
        while True:
            processed = await _poll_once(
                session_factory,
                prometheus_client,
                loki_client,
                clock,
                settings.soak_seconds,
            )
            if not processed:
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await prometheus_client.aclose()
        await loki_client.aclose()
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
