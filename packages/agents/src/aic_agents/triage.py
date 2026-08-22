"""Triage stage (design doc §1.4 TRIAGE row, T6): deterministic severity
assignment (`aic_domain.triage.assess_severity`) plus one cheap-tier LLM
call that writes a human-readable title/summary from the incident's
correlated signals. Fires `triaging -> investigating` on completion.

The state transition is computed *before* the LLM call (`transition()`
raises `IllegalTransition` immediately if the incident isn't in `TRIAGING`),
so a non-triageable incident never spends an LLM call.

Caller owns the transaction (wrap in `session_scope` or equivalent), the
same convention `aic_correlator.correlate.process_alert_event` uses.
"""

from __future__ import annotations

from uuid import UUID

from aic_common.clock import Clock
from aic_common.errors import NotFoundError
from aic_database.models import Incident, IncidentEvent, IncidentSignal
from aic_domain.enums import ActorType, IncidentTransitionEvent
from aic_domain.state_machine import transition
from aic_domain.triage import assess_severity
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aic_agents.port import LLMPort, ModelTier

_SYSTEM_PROMPT = (
    "You are an SRE incident triage assistant. Given a list of correlated "
    "alerts for one incident, write a short, specific, human-readable "
    "incident title and a one-to-two sentence summary. Only use facts "
    "present in the alerts below; never invent a root cause."
)


class _TriageTitle(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)


async def triage_incident(
    session: Session,
    incident_id: UUID,
    *,
    llm: LLMPort,
    clock: Clock,
) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError(f"no incident with id {incident_id}")

    # Computed up front so an illegal call (incident not in TRIAGING) fails
    # before any LLM spend, not after.
    next_status = transition(incident.status, IncidentTransitionEvent.TRIAGE_COMPLETED)

    signals = list(
        session.execute(
            select(IncidentSignal).where(IncidentSignal.incident_id == incident_id)
        )
        .scalars()
        .all()
    )
    severity = assess_severity(incident.service, incident.environment, len(signals))
    session.add(
        IncidentEvent(
            incident_id=incident_id,
            seq=_next_seq(session, incident_id),
            event_type="severity_assigned",
            actor_type=ActorType.SYSTEM,
            payload={"severity": severity.value, "signal_count": len(signals)},
            created_at=clock.now(),
        )
    )

    title_result = await llm.complete_structured(
        tier=ModelTier.CHEAP,
        agent_role="triage-title",
        system=_SYSTEM_PROMPT,
        user=_render_signals(incident, signals),
        response_model=_TriageTitle,
        incident_id=incident_id,
    )

    incident.title = title_result.title
    incident.summary = title_result.summary
    incident.severity = severity
    incident.status = next_status
    session.add(
        IncidentEvent(
            incident_id=incident_id,
            seq=_next_seq(session, incident_id),
            event_type=IncidentTransitionEvent.TRIAGE_COMPLETED.value,
            actor_type=ActorType.LLM,
            payload={"title": title_result.title, "summary": title_result.summary},
            created_at=clock.now(),
        )
    )
    return incident


def _render_signals(incident: Incident, signals: list[IncidentSignal]) -> str:
    lines = [
        f"Incident service group: {incident.service}, environment: {incident.environment.value}",
        f"{len(signals)} correlated alert(s):",
    ]
    for signal in signals:
        labels = ", ".join(f"{k}={v}" for k, v in sorted(signal.labels.items()))
        lines.append(f"- {signal.alertname} on {signal.service} ({labels})")
    return "\n".join(lines)


def _next_seq(session: Session, incident_id: UUID) -> int:
    stmt = select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
        IncidentEvent.incident_id == incident_id
    )
    result: int = session.execute(stmt).scalar_one()
    return result + 1
