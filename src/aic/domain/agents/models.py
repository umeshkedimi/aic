"""Agent domain models.

Models for AI agent orchestration (future implementation).
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class AgentType(str, Enum):
    """Types of AI agents."""

    RCA = "rca"
    LOG_ANALYSIS = "log_analysis"
    RUNBOOK = "runbook"
    KUBERNETES = "kubernetes"
    ROLLBACK = "rollback"
    ESCALATION = "escalation"
    TRIAGE = "triage"


class AgentExecutionStatus(str, Enum):
    """Status of an agent execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentExecution(BaseModel):
    """Record of an agent execution."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID | None = None
    agent_type: AgentType
    status: AgentExecutionStatus = AgentExecutionStatus.PENDING
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    @property
    def duration_ms(self) -> int | None:
        """Calculate execution duration in milliseconds."""
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None
