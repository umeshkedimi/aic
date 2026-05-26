"""Common type definitions used across the application."""

from typing import Annotated, NewType
from uuid import UUID

from pydantic import Field

# Entity IDs as NewTypes for type safety
IncidentId = NewType("IncidentId", UUID)
AnalysisId = NewType("AnalysisId", UUID)
DocumentId = NewType("DocumentId", UUID)
ChunkId = NewType("ChunkId", UUID)
AgentExecutionId = NewType("AgentExecutionId", UUID)

# Common field annotations for reuse
NonEmptyStr = Annotated[str, Field(min_length=1)]
ConfidenceScore = Annotated[float, Field(ge=0.0, le=1.0)]
Priority = Annotated[int, Field(ge=1, le=5)]
