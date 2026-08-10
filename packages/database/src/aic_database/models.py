"""ORM models for the `incident` schema.

Mirrors docs/design/09-database-design.md §9.3. Only the two Phase-1/2
tables are modeled here; evidence/RCA/proposal/approval tables land with
Phase 6-8 alongside the code that populates them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aic_database.base import Base

_SEVERITIES = ("sev1", "sev2", "sev3", "sev4")
_STATUSES = (
    "open",
    "triaging",
    "investigating",
    "awaiting_approval",
    "remediating",
    "verifying",
    "resolved",
    "closed",
    "escalated",
    "failed",
)
_ACTOR_TYPES = ("human", "agent", "system", "policy")


class IncidentModel(Base):
    __tablename__ = "incident"
    __table_args__ = (
        CheckConstraint(f"severity IN {_SEVERITIES}", name="ck_incident_severity"),
        CheckConstraint(f"status IN {_STATUSES}", name="ck_incident_status"),
        Index("ix_incident_service_created_at", "service", "created_at"),
        Index("ix_incident_fingerprint_created_at", "fingerprint", "created_at"),
        {"schema": "incident"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    fingerprint: Mapped[str]
    title: Mapped[str]
    summary: Mapped[str] = mapped_column(default="")
    severity: Mapped[str]
    status: Mapped[str]
    service: Mapped[str]
    environment: Mapped[str]
    source: Mapped[str]
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    slack_channel_id: Mapped[str | None] = mapped_column(default=None)
    jira_issue_key: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)

    events: Mapped[list[IncidentEventModel]] = relationship(
        back_populates="incident", order_by="IncidentEventModel.seq"
    )


class IncidentEventModel(Base):
    __tablename__ = "incident_event"
    __table_args__ = (
        CheckConstraint(f"actor_type IN {_ACTOR_TYPES}", name="ck_incident_event_actor_type"),
        UniqueConstraint("incident_id", "seq", name="uq_incident_event_incident_seq"),
        {"schema": "incident"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incident.incident.id"))
    seq: Mapped[int]
    event_type: Mapped[str]
    actor_type: Mapped[str]
    actor_id: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    incident: Mapped[IncidentModel] = relationship(back_populates="events")
