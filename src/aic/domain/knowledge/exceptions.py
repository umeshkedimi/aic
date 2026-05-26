"""Knowledge domain exceptions."""

from uuid import UUID


class KnowledgeError(Exception):
    """Base exception for knowledge domain errors."""

    pass


class DocumentNotFoundError(KnowledgeError):
    """Raised when a document cannot be found."""

    def __init__(self, document_id: UUID | str):
        self.document_id = document_id
        super().__init__(f"Document not found: {document_id}")


class ChunkingError(KnowledgeError):
    """Raised when document chunking fails."""

    pass


class EmbeddingError(KnowledgeError):
    """Raised when embedding generation fails."""

    pass
