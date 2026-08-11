"""SQLAlchemy 2.0 ORM models for the full schema (design doc §5).

One table per domain entity in `aic_domain.models`, same fields, same
FACT/Aggregate mutability intent — but this layer additionally owns the
foreign keys, indexes, and uniqueness/check constraints a persistence
layer is responsible for and a pure domain model is not. `IncidentStatus`
and the other closed-set enums are mapped straight from `aic_domain.enums`
so the state machine's value set has exactly one definition, not two that
can drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from aic_common.config import Environment
from aic_common.ids import new_id
from aic_domain.enums import (
    ActorType,
    ApprovalDecisionType,
    EvidenceStatus,
    IncidentStatus,
    PolicyEffect,
    Severity,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aic_database.base import Base


class Incident(Base):
    __tablename__ = "incident"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    fingerprint: Mapped[str] = mapped_column(sa.String, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(sa.String, default=None)
    summary: Mapped[str | None] = mapped_column(sa.Text, default=None)
    severity: Mapped[Severity | None] = mapped_column(
        sa.Enum(Severity, name="severity"), default=None
    )
    status: Mapped[IncidentStatus] = mapped_column(
        sa.Enum(IncidentStatus, name="incident_status"),
        default=IncidentStatus.OPEN,
        index=True,
    )
    service: Mapped[str] = mapped_column(sa.String, index=True)
    environment: Mapped[Environment] = mapped_column(sa.Enum(Environment, name="environment"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)


class IncidentEvent(Base):
    """Append-only audit spine. Application code must never UPDATE or DELETE
    a row here — every stage inserts one in the same transaction as any
    state change it makes."""

    __tablename__ = "incident_event"
    __table_args__ = (sa.UniqueConstraint("incident_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("incident.id"), index=True, nullable=False
    )
    seq: Mapped[int] = mapped_column(sa.Integer)
    event_type: Mapped[str] = mapped_column(sa.String)
    actor_type: Mapped[ActorType] = mapped_column(sa.Enum(ActorType, name="actor_type"))
    actor_id: Mapped[str | None] = mapped_column(sa.String, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class IncidentSignal(Base):
    __tablename__ = "incident_signal"
    __table_args__ = (sa.UniqueConstraint("incident_id", "alert_fingerprint"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("incident.id"), index=True, nullable=False
    )
    alert_fingerprint: Mapped[str] = mapped_column(sa.String)
    alertname: Mapped[str] = mapped_column(sa.String)
    service: Mapped[str] = mapped_column(sa.String)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    starts_at: Mapped[datetime] = mapped_column()


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("incident.id"), index=True, nullable=False
    )
    source: Mapped[str] = mapped_column(sa.String)
    tool: Mapped[str] = mapped_column(sa.String)
    query: Mapped[str | None] = mapped_column(sa.Text, default=None)
    result_digest: Mapped[str | None] = mapped_column(sa.Text, default=None)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    collected_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    status: Mapped[EvidenceStatus] = mapped_column(sa.Enum(EvidenceStatus, name="evidence_status"))

    __table_args__ = (
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_ms_non_negative"),
    )


class RCA(Base):
    __tablename__ = "rca"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("incident.id"), index=True, nullable=False
    )
    agent_version: Mapped[str] = mapped_column(sa.String)
    prompt_hash: Mapped[str | None] = mapped_column(sa.String, default=None)
    model: Mapped[str | None] = mapped_column(sa.String, default=None)
    status: Mapped[str] = mapped_column(sa.String)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class Hypothesis(Base):
    __tablename__ = "hypothesis"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    rca_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("rca.id"), index=True, nullable=False)
    rank: Mapped[int] = mapped_column(sa.Integer)
    statement: Mapped[str] = mapped_column(sa.Text)
    confidence: Mapped[float] = mapped_column(sa.Float)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    counter_evidence: Mapped[list[str]] = mapped_column(JSONB, default=list)
    demoted_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)

    __table_args__ = (
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="confidence_in_unit_interval"
        ),
    )


class RemediationProposal(Base):
    __tablename__ = "remediation_proposal"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("incident.id"), index=True, nullable=False
    )
    rca_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("rca.id"), index=True, nullable=False)
    rationale: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class Action(Base):
    __tablename__ = "action"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("remediation_proposal.id"), index=True, nullable=False
    )
    action_type: Mapped[str] = mapped_column(sa.String)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    target_resource: Mapped[str] = mapped_column(sa.String)
    policy_decision: Mapped[PolicyEffect | None] = mapped_column(
        sa.Enum(PolicyEffect, name="policy_effect"), default=None
    )
    status: Mapped[str] = mapped_column(sa.String)
    idempotency_key: Mapped[str] = mapped_column(sa.String, unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class PolicyDecision(Base):
    __tablename__ = "policy_decision"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    action_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("action.id"), index=True, nullable=False
    )
    rule_id: Mapped[str] = mapped_column(sa.String)
    rule_version: Mapped[int] = mapped_column(sa.Integer)
    effect: Mapped[PolicyEffect] = mapped_column(sa.Enum(PolicyEffect, name="policy_effect"))
    decided_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ApprovalRequest(Base):
    __tablename__ = "approval_request"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    action_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("action.id"), index=True, nullable=False
    )
    quorum: Mapped[int] = mapped_column(sa.Integer)
    required_roles: Mapped[list[str]] = mapped_column(JSONB, default=list)
    expires_at: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    status: Mapped[str] = mapped_column(sa.String, default="pending")


class ApprovalDecision(Base):
    """Immutable once cast (§1.10) — application code must never UPDATE a row
    here; enforcing that at the DB level (trigger/rule) is T9's job."""

    __tablename__ = "approval_decision"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("approval_request.id"), index=True, nullable=False
    )
    decider_id: Mapped[str] = mapped_column(sa.String)
    decision: Mapped[ApprovalDecisionType] = mapped_column(
        sa.Enum(ApprovalDecisionType, name="approval_decision_type")
    )
    reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
    decided_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ExecutionRecord(Base):
    __tablename__ = "execution_record"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    action_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("action.id"), index=True, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(sa.String)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class VerificationRecord(Base):
    __tablename__ = "verification_record"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("execution_record.id"), index=True, nullable=False
    )
    metric_snapshots: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    passed: Mapped[bool] = mapped_column(sa.Boolean)
    checked_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class Postmortem(Base):
    __tablename__ = "postmortem"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("incident.id"), index=True, nullable=False
    )
    content: Mapped[str] = mapped_column(sa.Text)
    embedding_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class ServiceDependency(Base):
    """Static seed config, not agent-written."""

    __tablename__ = "service_dependency"

    service: Mapped[str] = mapped_column(sa.String, primary_key=True)
    depends_on: Mapped[str] = mapped_column(sa.String, primary_key=True)


class Deployment(Base):
    __tablename__ = "deployment"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    service: Mapped[str] = mapped_column(sa.String, index=True)
    version: Mapped[str] = mapped_column(sa.String)
    image_tag: Mapped[str] = mapped_column(sa.String)
    config_diff: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    deployed_at: Mapped[datetime] = mapped_column(index=True)
    deployed_by: Mapped[str] = mapped_column(sa.String)
