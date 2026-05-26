"""Incident domain models.

These are the core business entities for incidents, separate from
database models and API schemas.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class IncidentSeverity(str, Enum):
    """Incident severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IncidentStatus(str, Enum):
    """Incident lifecycle status."""

    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Incident(BaseModel):
    """Core incident domain model."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    external_id: str | None = None
    title: str
    description: str | None = None
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.OPEN
    source: str
    service: str | None = None
    environment: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """Check if incident is still active (not resolved or closed)."""
        return self.status not in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED)

    @property
    def is_critical(self) -> bool:
        """Check if incident is critical severity."""
        return self.severity == IncidentSeverity.CRITICAL


class IncidentCreate(BaseModel):
    """Data required to create a new incident."""

    external_id: str | None = None
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    severity: IncidentSeverity
    source: str = Field(..., min_length=1, max_length=100)
    service: str | None = Field(default=None, max_length=255)
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentUpdate(BaseModel):
    """Data for updating an existing incident."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    severity: IncidentSeverity | None = None
    status: IncidentStatus | None = None
    service: str | None = Field(default=None, max_length=255)
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
