"""The Incident aggregate.

The only object permitted to change `status` is this class, and the only
function it delegates that decision to is `transition` (state.py). Every
call to `apply` appends exactly one IncidentEvent — the aggregate and its
own audit trail cannot drift apart because they're the same method call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from aic_common.clock import Clock
from aic_common.ids import new_id
from aic_domain.incidents.events import ActorType, IncidentEvent
from aic_domain.incidents.severity import Severity
from aic_domain.incidents.state import IncidentStatus, IncidentTransitionEvent, transition


class Incident:
    def __init__(
        self,
        *,
        id: UUID,
        fingerprint: str,
        title: str,
        service: str,
        environment: str,
        source: str,
        severity: Severity,
        status: IncidentStatus,
        created_at: datetime,
        updated_at: datetime,
        summary: str = "",
        resolved_at: datetime | None = None,
        seq: int = 0,
    ) -> None:
        self.id = id
        self.fingerprint = fingerprint
        self.title = title
        self.summary = summary
        self.service = service
        self.environment = environment
        self.source = source
        self.severity = severity
        self.status = status
        self.created_at = created_at
        self.updated_at = updated_at
        self.resolved_at = resolved_at
        self._seq = seq
        self._pending_events: list[IncidentEvent] = []

    @classmethod
    def open(
        cls,
        *,
        fingerprint: str,
        title: str,
        service: str,
        environment: str,
        source: str,
        severity: Severity,
        clock: Clock,
    ) -> Incident:
        now = clock.now()
        incident = cls(
            id=new_id(),
            fingerprint=fingerprint,
            title=title,
            service=service,
            environment=environment,
            source=source,
            severity=severity,
            status=IncidentStatus.OPEN,
            created_at=now,
            updated_at=now,
        )
        incident._record(
            event_type="created",
            actor_type=ActorType.SYSTEM,
            actor_id="aic-ingest",
            clock=clock,
            payload={"fingerprint": fingerprint, "source": source},
        )
        return incident

    def apply(
        self,
        event: IncidentTransitionEvent,
        *,
        actor_type: ActorType,
        actor_id: str,
        clock: Clock,
        payload: dict[str, Any] | None = None,
    ) -> IncidentEvent:
        """Move the incident to its next status and append the audit event.

        Raises IllegalTransition (via `transition`) if `event` is not valid
        from the current status — callers should treat that as a 409, not
        catch-and-ignore.
        """
        new_status = transition(self.status, event)
        self.status = new_status
        self.updated_at = clock.now()
        if new_status in (IncidentStatus.RESOLVED,) and self.resolved_at is None:
            self.resolved_at = self.updated_at
        return self._record(
            event_type=event.value,
            actor_type=actor_type,
            actor_id=actor_id,
            clock=clock,
            payload=payload or {},
        )

    def pending_events(self) -> list[IncidentEvent]:
        """Events appended since construction, for the repository to persist."""
        return list(self._pending_events)

    def clear_pending_events(self) -> None:
        self._pending_events.clear()

    def _record(
        self,
        *,
        event_type: str,
        actor_type: ActorType,
        actor_id: str,
        clock: Clock,
        payload: dict[str, Any],
    ) -> IncidentEvent:
        self._seq += 1
        event = IncidentEvent(
            id=new_id(),
            incident_id=self.id,
            seq=self._seq,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            created_at=clock.now(),
        )
        self._pending_events.append(event)
        return event
