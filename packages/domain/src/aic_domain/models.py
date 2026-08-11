"""Pure, zero-I/O domain models (design doc §5).

Every entity is tagged FACT / INFERENCE / HYPOTHESIS / RECOMMENDATION /
ACTION / RESULT in the design doc per the project's evidence-first
principle; that tag decides whether the model here is a `Fact` (frozen —
append-only, never mutated once written) or an `Aggregate` (has a
lifecycle, fields change as the workflow progresses). `Incident` and
`ApprovalDecision`/`ApprovalRequest` immutability in particular reflect
explicit design requirements (§5, §1.10): the audit spine and approval
decisions must never be editable after the fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from aic_common.config import Environment
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from aic_domain.enums import (
    ActorType,
    ApprovalDecisionType,
    EvidenceStatus,
    IncidentStatus,
    PolicyEffect,
    Severity,
)


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime fields must be timezone-aware")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_timezone)]


class Fact(BaseModel):
    """Base for append-only records: constructed once, never mutated."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Aggregate(BaseModel):
    """Base for records with a lifecycle: fields legitimately change over time."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Incident(Aggregate):
    id: UUID
    fingerprint: str
    title: str | None = None
    summary: str | None = None
    severity: Severity | None = None
    status: IncidentStatus = IncidentStatus.OPEN
    service: str
    environment: Environment
    created_at: UtcDatetime
    resolved_at: UtcDatetime | None = None


class IncidentEvent(Fact):
    """Append-only audit spine. Every stage writes one in the same transaction
    as any state change it makes, so state and history can never disagree."""

    id: UUID
    incident_id: UUID
    seq: int = Field(ge=1)
    event_type: str
    actor_type: ActorType
    actor_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDatetime


class IncidentSignal(Fact):
    id: UUID
    incident_id: UUID
    alert_fingerprint: str
    alertname: str
    service: str
    labels: dict[str, str] = Field(default_factory=dict)
    starts_at: UtcDatetime


class Evidence(Fact):
    id: UUID
    incident_id: UUID
    source: str
    tool: str
    query: str | None = None
    result_digest: str | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    collected_at: UtcDatetime
    status: EvidenceStatus


class Hypothesis(Aggregate):
    """Mutable because self-check (§1.7) can demote a hypothesis after the
    fact, recording why, rather than silently trusting it."""

    id: UUID
    rca_id: UUID
    rank: int = Field(ge=1)
    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[UUID] = Field(default_factory=list)
    counter_evidence: list[UUID] = Field(default_factory=list)
    demoted_reason: str | None = None


class RCA(Aggregate):
    id: UUID
    incident_id: UUID
    agent_version: str
    prompt_hash: str | None = None
    model: str | None = None
    status: str
    created_at: UtcDatetime


class RemediationProposal(Fact):
    id: UUID
    incident_id: UUID
    rca_id: UUID
    rationale: str
    created_at: UtcDatetime


class Action(Aggregate):
    id: UUID
    proposal_id: UUID
    action_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    target_resource: str
    policy_decision: PolicyEffect | None = None
    status: str
    idempotency_key: str
    created_at: UtcDatetime


class PolicyDecision(Fact):
    id: UUID
    action_id: UUID
    rule_id: str
    rule_version: int = Field(ge=1)
    effect: PolicyEffect
    decided_at: UtcDatetime


class ApprovalRequest(Aggregate):
    id: UUID
    action_id: UUID
    quorum: int = Field(ge=1)
    required_roles: list[str] = Field(default_factory=list)
    expires_at: UtcDatetime
    created_at: UtcDatetime
    status: str = "pending"


class ApprovalDecision(Fact):
    """Immutable once cast (§1.10): a decider cannot change their vote."""

    id: UUID
    approval_request_id: UUID
    decider_id: str
    decision: ApprovalDecisionType
    reason: str | None = None
    decided_at: UtcDatetime


class ExecutionRecord(Aggregate):
    id: UUID
    action_id: UUID
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    status: str
    output: dict[str, Any] | None = None


class VerificationRecord(Fact):
    id: UUID
    execution_id: UUID
    metric_snapshots: dict[str, Any] = Field(default_factory=dict)
    passed: bool
    checked_at: UtcDatetime


class Postmortem(Aggregate):
    id: UUID
    incident_id: UUID
    content: str
    embedding_refs: list[str] = Field(default_factory=list)
    created_at: UtcDatetime


class ServiceDependency(Fact):
    """Static seed config, not agent-written."""

    service: str
    depends_on: str


class Deployment(Fact):
    id: UUID
    service: str
    version: str
    image_tag: str
    config_diff: dict[str, Any] = Field(default_factory=dict)
    deployed_at: UtcDatetime
    deployed_by: str
