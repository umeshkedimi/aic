"""Correlation logic (design doc §1.4 CORRELATE row): dependency-graph +
time-window grouping. Fully deterministic — no LLM involvement, since
grouping by a static graph and a time window is exact and auditable.

Design call not fully pinned down by the doc (documented here rather than
blocking on it, per CLAUDE.local.md's guidance): `Incident.fingerprint`
doubles as the correlation identity — "the rest attach as IncidentSignal
rows... share one incident fingerprint" — but the schema (T1) gives it a
plain unique-string shape, and the "5-minute rolling window" needs *some*
time anchor. This implementation anchors the window to the incident's own
`created_at` (i.e. the first alert's arrival): a later alert for the same
dependency group attaches to the existing incident if that incident was
created within `window` of the later alert's `starts_at`; otherwise a new
incident opens. `fingerprint` is built as `f"{group_key}:{starts_at}"` from
the *first* alert, so it's unique per incident by construction (the DB's
unique constraint remains the real backstop, not just documentation).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from aic_common.clock import Clock
from aic_common.ids import new_id
from aic_contracts.events import AlertEvent
from aic_database.models import Incident, IncidentEvent, IncidentSignal
from aic_domain.correlation import ServiceDependencyGraph
from aic_domain.enums import ActorType, IncidentStatus, IncidentTransitionEvent
from aic_domain.state_machine import transition
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

CORRELATION_WINDOW = timedelta(minutes=5)

_CLOSED_STATUSES = frozenset({IncidentStatus.RESOLVED, IncidentStatus.CLOSED})


def process_alert_event(
    session: Session,
    event: AlertEvent,
    *,
    graph: ServiceDependencyGraph,
    clock: Clock,
    window: timedelta = CORRELATION_WINDOW,
) -> uuid.UUID:
    """Attach `event` to an existing open Incident within the correlation
    window for its dependency group, or open a new one. Returns the
    Incident id.

    Idempotent under Kafka's at-least-once delivery: a replayed
    `alert_fingerprint` against an incident it's already attached to hits
    `IncidentSignal`'s `(incident_id, alert_fingerprint)` unique
    constraint, caught here as an expected no-op rather than left to crash
    the consumer loop.

    Caller owns the transaction (wrap in `aic_database.session.session_scope`
    or equivalent) — every write here, including the state transition's
    audit event, must land atomically or not at all.
    """
    group_key = graph.group_key(event.service)

    incident = _find_open_incident(session, group_key, event.starts_at, window)
    is_new = incident is None
    if incident is None:
        incident = Incident(
            id=new_id(),
            fingerprint=f"{group_key}:{event.starts_at.isoformat()}",
            service=group_key,
            environment=event.environment,
            status=IncidentStatus.OPEN,
            created_at=clock.now(),
        )
        session.add(incident)
        session.flush()
    incident_id: uuid.UUID = incident.id

    try:
        with session.begin_nested():
            session.add(
                IncidentSignal(
                    id=new_id(),
                    incident_id=incident_id,
                    alert_fingerprint=event.alert_fingerprint,
                    alertname=event.alertname,
                    service=event.service,
                    labels=event.labels,
                    starts_at=event.starts_at,
                )
            )
            session.flush()
    except IntegrityError:
        return incident_id  # already recorded — at-least-once replay, not a new signal

    _append_event(
        session,
        incident_id,
        event_type="alert_signal_attached",
        payload={"alert_fingerprint": event.alert_fingerprint, "alertname": event.alertname},
        clock=clock,
    )

    if is_new:
        incident.status = transition(incident.status, IncidentTransitionEvent.WORKFLOW_STARTED)
        _append_event(
            session,
            incident_id,
            event_type=IncidentTransitionEvent.WORKFLOW_STARTED.value,
            payload={},
            clock=clock,
        )

    return incident_id


def _find_open_incident(
    session: Session, group_key: str, starts_at: datetime, window: timedelta
) -> Incident | None:
    stmt = (
        select(Incident)
        .where(Incident.fingerprint.startswith(f"{group_key}:"))
        .where(Incident.status.not_in(_CLOSED_STATUSES))
        .where(Incident.created_at >= starts_at - window)
        .order_by(Incident.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    return session.execute(stmt).scalar_one_or_none()


def _next_seq(session: Session, incident_id: uuid.UUID) -> int:
    stmt = select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
        IncidentEvent.incident_id == incident_id
    )
    result: int = session.execute(stmt).scalar_one()
    return result + 1


def _append_event(
    session: Session,
    incident_id: uuid.UUID,
    *,
    event_type: str,
    payload: dict[str, object],
    clock: Clock,
    actor_type: ActorType = ActorType.SYSTEM,
) -> None:
    session.add(
        IncidentEvent(
            id=new_id(),
            incident_id=incident_id,
            seq=_next_seq(session, incident_id),
            event_type=event_type,
            actor_type=actor_type,
            payload=payload,
            created_at=clock.now(),
        )
    )
