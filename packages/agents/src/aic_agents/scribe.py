"""Learning stage (design doc §1.4 LEARN row, §1.13, T12): one cheap-tier
LLM call ("scribe") drafts a structured `Postmortem` from the incident's
full `IncidentEvent` audit log plus its RCA/Hypothesis and remediation
outcome. The drafted content is chunked, embedded, and indexed into Qdrant
(`aic_agents.knowledge_store`) so a later incident's real `knowledge.search()`
(`aic_agents.tools.knowledge`) can retrieve it — the design doc's own test
of a closed learning loop.

**`failure_mode` comes from the same LLM call, not a second one.** The
scribe call already reads the full incident narrative to draft the
postmortem; asking it to also name a short failure-mode tag
(`"db_connection_pool_exhaustion"`) as one more field on that same
structured output is language understanding over facts already on the
audit spine — exactly the kind of LLM use §1.4's LEARN row endorses — not
an extra call. `resolution_action_type` is the opposite: deterministic,
read straight off the incident's own `Action.action_type` (no LLM
judgment involved in "what action type did we actually take").

**A design call the doc doesn't pin down: firing `POST_REVIEW` here.** T1's
state machine (§6) has always had a real `RESOLVED -> CLOSED` edge
(`IncidentTransitionEvent.POST_REVIEW`), but no stage through T11 ever
fires it — nothing in §1.4's table names a "post-review" stage
separately from LEARN. Since a drafted, indexed postmortem *is* the
post-incident review this project's state machine already models, this
stage fires `POST_REVIEW` once the `Postmortem` row is persisted and
indexed, moving the incident to `CLOSED`. Keeps a real diagram edge from
staying permanently dead code rather than inventing a new one.

Caller owns the transaction (same `Session`-taking, caller-owns-the-
transaction convention as `aic_agents.triage.triage_incident` /
`aic_agents.remediation.plan_remediation`) — this stage's own work
(one LLM call, one Qdrant upsert, a handful of small reads) is bounded and
fast, unlike T11's verifier, so there's no reason to depart from the
single-session pattern for a long-lived external wait the way T11 did.
"""

from __future__ import annotations

from uuid import UUID

from aic_common.clock import Clock
from aic_common.errors import NotFoundError
from aic_common.ids import new_id
from aic_database.models import Action as ActionRow
from aic_database.models import ExecutionRecord as ExecutionRecordRow
from aic_database.models import Incident as IncidentRow
from aic_database.models import IncidentEvent
from aic_database.models import Postmortem as PostmortemRow
from aic_database.models import RemediationProposal as RemediationProposalRow
from aic_domain.enums import ActorType, IncidentTransitionEvent
from aic_domain.state_machine import transition
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aic_agents.knowledge_store import QdrantSettings, index_postmortem
from aic_agents.port import LLMPort, ModelTier

_SYSTEM_PROMPT = (
    "You are an SRE writing a postmortem for a resolved incident. Given the "
    "incident's full audit log (title, severity, root-cause hypothesis, "
    "the remediation action taken, and its outcome), write a structured "
    "postmortem: a short narrative timeline, a one-sentence root-cause "
    "summary, what action was taken, and the outcome. Also classify the "
    "failure with a short, specific, machine-readable tag (snake_case, "
    "e.g. 'db_connection_pool_exhaustion') that a future similar incident "
    "could be tagged with too. Only use facts present in the log; never "
    "invent details."
)


class _ScribeOutput(BaseModel):
    timeline: str = Field(min_length=1, max_length=4000)
    root_cause_summary: str = Field(min_length=1, max_length=1000)
    action_taken: str = Field(min_length=1, max_length=1000)
    outcome: str = Field(min_length=1, max_length=1000)
    failure_mode: str = Field(min_length=1, max_length=100)


def _render_content(incident: IncidentRow, result: _ScribeOutput) -> str:
    return (
        f"# Postmortem: {incident.title or incident.fingerprint}\n\n"
        f"**Service:** {incident.service}  **Severity:** "
        f"{incident.severity.value if incident.severity else 'unknown'}  "
        f"**Failure mode:** {result.failure_mode}\n\n"
        f"## Timeline\n{result.timeline}\n\n"
        f"## Root cause\n{result.root_cause_summary}\n\n"
        f"## Action taken\n{result.action_taken}\n\n"
        f"## Outcome\n{result.outcome}\n"
    )


def _render_event_log(events: list[IncidentEvent]) -> str:
    lines = []
    for event in events:
        lines.append(f"- [{event.created_at.isoformat()}] {event.event_type} ({event.actor_type})")
    return "\n".join(lines)


def _find_resolution_action_type(session: Session, incident_id: UUID) -> str | None:
    """The action type of the incident's most recent execution, straight
    off `Action.action_type` — deterministic, same
    `ExecutionRecord -> Action -> RemediationProposal` join T11's verifier
    uses to find the same execution."""
    return session.execute(
        select(ActionRow.action_type)
        .join(ExecutionRecordRow, ExecutionRecordRow.action_id == ActionRow.id)
        .join(RemediationProposalRow, RemediationProposalRow.id == ActionRow.proposal_id)
        .where(RemediationProposalRow.incident_id == incident_id)
        .order_by(ExecutionRecordRow.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _next_seq(session: Session, incident_id: UUID) -> int:
    stmt = select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
        IncidentEvent.incident_id == incident_id
    )
    result: int = session.execute(stmt).scalar_one()
    return result + 1


async def draft_postmortem(
    session: Session,
    incident_id: UUID,
    *,
    llm: LLMPort,
    clock: Clock,
    qdrant_client: AsyncQdrantClient,
    qdrant_settings: QdrantSettings,
) -> PostmortemRow:
    incident = session.get(IncidentRow, incident_id)
    if incident is None:
        raise NotFoundError(f"no incident with id {incident_id}")

    # Computed up front, same "fail fast before any LLM/Qdrant spend"
    # convention as triage/remediation: an incident that isn't RESOLVED
    # never reaches the LLM call below.
    next_status = transition(incident.status, IncidentTransitionEvent.POST_REVIEW)

    events = list(
        session.execute(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(IncidentEvent.seq)
        )
        .scalars()
        .all()
    )
    resolution_action_type = _find_resolution_action_type(session, incident_id)

    user = (
        f"Incident: {incident.title or incident.fingerprint}\n"
        f"Service: {incident.service}\n"
        f"Severity: {incident.severity.value if incident.severity else 'unknown'}\n"
        f"Summary: {incident.summary or ''}\n\n"
        f"Audit log ({len(events)} events):\n{_render_event_log(events)}"
    )
    result = await llm.complete_structured(
        tier=ModelTier.CHEAP,
        agent_role="scribe",
        system=_SYSTEM_PROMPT,
        user=user,
        response_model=_ScribeOutput,
        incident_id=incident_id,
    )

    content = _render_content(incident, result)
    postmortem_id = new_id()
    embedding_refs = await index_postmortem(
        client=qdrant_client,
        settings=qdrant_settings,
        postmortem_id=postmortem_id,
        incident_id=incident_id,
        service=incident.service,
        failure_mode=result.failure_mode,
        resolution_action_type=resolution_action_type,
        content=content,
    )

    now = clock.now()
    postmortem = PostmortemRow(
        id=postmortem_id,
        incident_id=incident_id,
        content=content,
        embedding_refs=embedding_refs,
        created_at=now,
    )
    session.add(postmortem)
    session.flush()

    incident.status = next_status
    session.add(
        IncidentEvent(
            incident_id=incident_id,
            seq=_next_seq(session, incident_id),
            event_type=IncidentTransitionEvent.POST_REVIEW.value,
            actor_type=ActorType.LLM,
            payload={
                "postmortem_id": str(postmortem_id),
                "failure_mode": result.failure_mode,
                "chunks_indexed": len(embedding_refs),
            },
            created_at=now,
        )
    )
    return postmortem
