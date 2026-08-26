"""aic-approval-expirer (design doc §1.10 "expiry + escalation ladder", T9).

Polls Postgres for `ApprovalRequest` rows still `pending` past their
`expires_at` and calls `aic_agents.approval.expire_request` on the oldest
one, same host-process pattern as aic-triage/aic-investigator/
aic-remediator (T6/T7/T8). Exists for the same reason those pollers exist:
a request nobody ever decides on has nothing else that will ever notice it
timed out — `record_decision` only rejects a late decision attempt, it
never itself performs the expiry+escalation transition (see
`aic_agents.approval`'s own module docstring for why).

No `SELECT ... FOR UPDATE`: single instance for the demo, same assumption
every other poller in this codebase makes.
"""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

from aic_agents.approval import expire_request
from aic_common.clock import Clock, SystemClock
from aic_common.logging import configure_logging, get_logger
from aic_database.models import ApprovalRequest
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from aic_domain.enums import ApprovalRequestStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aic_approval_expirer.config import ApprovalExpirerSettings

logger = get_logger(__name__)


def _find_next_due_request_id(
    session_factory: sessionmaker[Session], *, clock: Clock
) -> UUID | None:
    with session_scope(session_factory) as session:
        result: UUID | None = session.execute(
            select(ApprovalRequest.id)
            .where(ApprovalRequest.status == ApprovalRequestStatus.PENDING.value)
            .where(ApprovalRequest.expires_at <= clock.now())
            .order_by(ApprovalRequest.expires_at)
            .limit(1)
        ).scalar_one_or_none()
        return result


async def _poll_once(session_factory: sessionmaker[Session], clock: Clock) -> bool:
    request_id = _find_next_due_request_id(session_factory, clock=clock)
    if request_id is None:
        return False

    with session_scope(session_factory) as session:
        expire_request(session, request_id, clock=clock)
        logger.info("aic_approval_expirer.request_expired", approval_request_id=str(request_id))
    return True


async def run(settings: ApprovalExpirerSettings | None = None) -> None:
    settings = settings or ApprovalExpirerSettings()
    configure_logging(settings.log_level)
    clock = SystemClock()

    db_settings = DatabaseSettings(url=os.environ["AIC_DATABASE_URL"])
    engine = create_database_engine(db_settings)
    session_factory = create_session_factory(engine)

    logger.info(
        "aic_approval_expirer.started", poll_interval_seconds=settings.poll_interval_seconds
    )
    try:
        while True:
            processed = await _poll_once(session_factory, clock)
            if not processed:
                await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        engine.dispose()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
