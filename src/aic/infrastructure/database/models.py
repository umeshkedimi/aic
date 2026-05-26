"""SQLAlchemy ORM models.

Database table definitions using SQLAlchemy 2.0 declarative style.
These models map directly to PostgreSQL tables.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    String,
    Text,
    Integer,
    Boolean,
    Numeric,
    ForeignKey,
    Index,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID, JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[str]: JSONB,
        list[UUID]: JSONB,
    }


class IncidentModel(Base):
    """SQLAlchemy model for incidents table."""

    __tablename__ = "incidents"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    analyses: Mapped[list["IncidentAnalysisModel"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )
    events: Mapped[list["IncidentEventModel"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_incidents_status", "status"),
        Index("idx_incidents_severity", "severity"),
        Index("idx_incidents_service", "service"),
        Index("idx_incidents_created_at", "created_at"),
        Index("idx_incidents_source", "source"),
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name="valid_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'investigating', 'mitigated', 'resolved', 'closed')",
            name="valid_status",
        ),
    )


class IncidentAnalysisModel(Base):
    """SQLAlchemy model for incident analyses."""

    __tablename__ = "incident_analyses"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    incident_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    analysis_type: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    severity_assessment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    suggested_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    related_incidents: Mapped[list[UUID]] = mapped_column(JSONB, default=list)
    context_used: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    # Relationships
    incident: Mapped["IncidentModel"] = relationship(back_populates="analyses")

    __table_args__ = (
        Index("idx_analyses_incident", "incident_id"),
        Index("idx_analyses_type", "analysis_type"),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="valid_confidence",
        ),
    )


class IncidentEventModel(Base):
    """SQLAlchemy model for incident timeline events."""

    __tablename__ = "incident_events"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    incident_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    # Relationships
    incident: Mapped["IncidentModel"] = relationship(back_populates="events")

    __table_args__ = (
        Index("idx_events_incident", "incident_id"),
        Index("idx_events_type", "event_type"),
        Index("idx_events_created", "created_at"),
    )


class KnowledgeDocumentModel(Base):
    """SQLAlchemy model for knowledge documents."""

    __tablename__ = "knowledge_documents"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    chunks: Mapped[list["KnowledgeChunkModel"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_knowledge_type", "doc_type"),
        Index("idx_knowledge_service", "service"),
        Index("idx_knowledge_active", "is_active"),
    )


class KnowledgeChunkModel(Base):
    """SQLAlchemy model for knowledge chunks (for RAG)."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    # Relationships
    document: Mapped["KnowledgeDocumentModel"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("idx_chunks_document", "document_id"),
        Index(
            "idx_chunks_doc_index",
            "document_id",
            "chunk_index",
            unique=True,
        ),
    )


class AgentExecutionModel(Base):
    """SQLAlchemy model for agent executions."""

    __tablename__ = "agent_executions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    incident_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    input_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("idx_agent_exec_incident", "incident_id"),
        Index("idx_agent_exec_status", "status"),
    )
