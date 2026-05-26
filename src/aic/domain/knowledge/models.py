"""Knowledge base domain models.

Represents operational knowledge documents like runbooks, SOPs,
and architecture documentation used for RAG retrieval.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class DocumentType(str, Enum):
    """Type of knowledge document."""

    RUNBOOK = "runbook"
    SOP = "sop"
    ARCHITECTURE = "architecture"
    POSTMORTEM = "postmortem"
    FAQ = "faq"
    TROUBLESHOOTING = "troubleshooting"


class KnowledgeDocument(BaseModel):
    """A knowledge base document."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str = Field(..., min_length=1, max_length=500)
    content: str
    doc_type: DocumentType
    service: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None


class KnowledgeDocumentCreate(BaseModel):
    """Data required to create a knowledge document."""

    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    doc_type: DocumentType
    service: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None


class KnowledgeChunk(BaseModel):
    """A chunk of a knowledge document for RAG retrieval."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    token_count: int | None = None
    embedding_id: str | None = None
    created_at: datetime


class RetrievedContext(BaseModel):
    """Context retrieved from the knowledge base via RAG."""

    chunk_id: UUID
    document_id: UUID
    document_title: str
    doc_type: DocumentType
    content: str
    score: float
    service: str | None = None
