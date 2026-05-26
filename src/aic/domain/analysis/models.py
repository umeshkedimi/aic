"""Analysis domain models.

Represents AI-generated incident analysis including RCA,
summaries, and suggested actions.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class AnalysisType(str, Enum):
    """Type of analysis performed."""

    RCA = "rca"
    SUMMARY = "summary"
    TRIAGE = "triage"


class RootCauseHypothesis(BaseModel):
    """A hypothesis about the root cause of an incident."""

    description: str = Field(..., description="Description of the potential root cause")
    likelihood: float = Field(
        ..., ge=0.0, le=1.0, description="Probability score 0-1"
    )
    evidence: list[str] = Field(
        default_factory=list, description="Supporting evidence"
    )
    category: str | None = Field(
        default=None, description="Category (e.g., infrastructure, application, config)"
    )


class SuggestedAction(BaseModel):
    """A suggested action for incident response."""

    description: str = Field(..., description="Action description")
    priority: int = Field(..., ge=1, le=5, description="Priority 1 (highest) to 5")
    action_type: str = Field(
        ..., description="Type: investigate, mitigate, escalate, communicate"
    )
    estimated_time: str | None = Field(default=None, description="Estimated time to complete")
    runbook_reference: str | None = Field(default=None, description="Related runbook ID or URL")


class SeverityAssessment(BaseModel):
    """AI assessment of incident severity."""

    level: str = Field(..., description="Assessed severity level")
    reasoning: str = Field(..., description="Explanation for the assessment")
    blast_radius: str = Field(..., description="Affected scope description")
    user_impact: str | None = Field(default=None, description="Impact on users")


class IncidentAnalysis(BaseModel):
    """Complete AI-generated incident analysis."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    analysis_type: AnalysisType

    summary: str = Field(..., max_length=1000)
    root_cause_hypotheses: list[RootCauseHypothesis] = Field(
        default_factory=list, max_length=5
    )
    severity_assessment: SeverityAssessment | None = None
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0)

    related_incidents: list[UUID] = Field(default_factory=list)
    context_sources: list[str] = Field(
        default_factory=list, description="RAG sources used"
    )

    model_used: str | None = None
    tokens_used: int | None = None
    latency_ms: int | None = None

    created_at: datetime


class AnalysisRequest(BaseModel):
    """Request to analyze an incident."""

    incident_id: UUID
    analysis_type: AnalysisType = AnalysisType.RCA
    include_rag: bool = True
    max_hypotheses: int = Field(default=3, ge=1, le=5)
    additional_context: dict[str, Any] = Field(default_factory=dict)
