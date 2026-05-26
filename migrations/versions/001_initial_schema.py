"""Initial database schema.

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Incidents table
    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="open"),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("service", sa.String(255), nullable=True),
        sa.Column("environment", sa.String(50), nullable=True),
        sa.Column("tags", postgresql.JSONB(), server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name="valid_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'investigating', 'mitigated', 'resolved', 'closed')",
            name="valid_status",
        ),
    )
    op.create_index("idx_incidents_status", "incidents", ["status"])
    op.create_index("idx_incidents_severity", "incidents", ["severity"])
    op.create_index("idx_incidents_service", "incidents", ["service"])
    op.create_index("idx_incidents_created_at", "incidents", ["created_at"])
    op.create_index("idx_incidents_source", "incidents", ["source"])

    # Incident analyses table
    op.create_table(
        "incident_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_type", sa.String(50), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("root_cause_hypothesis", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("severity_assessment", sa.String(20), nullable=True),
        sa.Column("suggested_actions", postgresql.JSONB(), server_default="[]"),
        sa.Column("related_incidents", postgresql.JSONB(), server_default="[]"),
        sa.Column("context_used", postgresql.JSONB(), server_default="{}"),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="valid_confidence",
        ),
    )
    op.create_index("idx_analyses_incident", "incident_analyses", ["incident_id"])
    op.create_index("idx_analyses_type", "incident_analyses", ["analysis_type"])

    # Incident events table
    op.create_table(
        "incident_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(), server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_events_incident", "incident_events", ["incident_id"])
    op.create_index("idx_events_type", "incident_events", ["event_type"])
    op.create_index("idx_events_created", "incident_events", ["created_at"])

    # Knowledge documents table
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("service", sa.String(255), nullable=True),
        sa.Column("tags", postgresql.JSONB(), server_default="[]"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_knowledge_type", "knowledge_documents", ["doc_type"])
    op.create_index("idx_knowledge_service", "knowledge_documents", ["service"])
    op.create_index("idx_knowledge_active", "knowledge_documents", ["is_active"])

    # Knowledge chunks table
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_chunks_document", "knowledge_chunks", ["document_id"])
    op.create_index(
        "idx_chunks_doc_index",
        "knowledge_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )

    # Agent executions table
    op.create_table(
        "agent_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("input_data", postgresql.JSONB(), server_default="{}"),
        sa.Column("output_data", postgresql.JSONB(), server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("idx_agent_exec_incident", "agent_executions", ["incident_id"])
    op.create_index("idx_agent_exec_status", "agent_executions", ["status"])


def downgrade() -> None:
    op.drop_table("agent_executions")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("incident_events")
    op.drop_table("incident_analyses")
    op.drop_table("incidents")
