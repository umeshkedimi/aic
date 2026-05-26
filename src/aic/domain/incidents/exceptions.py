"""Incident domain exceptions."""

from uuid import UUID


class IncidentError(Exception):
    """Base exception for incident domain errors."""

    pass


class IncidentNotFoundError(IncidentError):
    """Raised when an incident cannot be found."""

    def __init__(self, incident_id: UUID | str):
        self.incident_id = incident_id
        super().__init__(f"Incident not found: {incident_id}")


class IncidentValidationError(IncidentError):
    """Raised when incident data is invalid."""

    def __init__(self, message: str, field: str | None = None):
        self.field = field
        super().__init__(message)


class DuplicateIncidentError(IncidentError):
    """Raised when attempting to create a duplicate incident."""

    def __init__(self, external_id: str):
        self.external_id = external_id
        super().__init__(f"Incident with external_id already exists: {external_id}")
