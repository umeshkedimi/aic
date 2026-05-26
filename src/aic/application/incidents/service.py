"""Incident service - application layer use cases.

Orchestrates incident operations including CRUD, validation,
and event emission.
"""

from typing import Sequence
from uuid import UUID

from aic.domain.incidents import (
    Incident,
    IncidentCreate,
    IncidentUpdate,
    IncidentSeverity,
    IncidentStatus,
    IncidentNotFoundError,
)
from aic.infrastructure.database.session import get_session
from aic.infrastructure.database.repositories.incidents import IncidentRepository
from aic.infrastructure.database.models import IncidentModel
from aic.shared import utc_now
from aic.observability.logging import get_logger
from aic.observability.metrics import INCIDENTS_TOTAL, INCIDENTS_ACTIVE

logger = get_logger(__name__)


class IncidentService:
    """Service for incident management operations."""

    async def create_incident(self, data: IncidentCreate) -> Incident:
        """Create a new incident.

        Args:
            data: Incident creation data

        Returns:
            The created incident

        Raises:
            DuplicateIncidentError: If external_id already exists
        """
        async with get_session() as session:
            repo = IncidentRepository(session)

            # Check for duplicate external_id
            if data.external_id:
                existing = await repo.get_by_external_id(data.external_id)
                if existing:
                    from aic.domain.incidents.exceptions import DuplicateIncidentError
                    raise DuplicateIncidentError(data.external_id)

            # Create the incident
            incident_model = repo.create_incident_model(
                title=data.title,
                description=data.description,
                severity=data.severity.value,
                source=data.source,
                external_id=data.external_id,
                service=data.service,
                environment=data.environment,
                tags=data.tags,
                metadata=data.metadata,
            )

            incident_model = await repo.create(incident_model)

            logger.info(
                "Incident created",
                incident_id=str(incident_model.id),
                severity=data.severity.value,
                source=data.source,
            )

            # Record metrics
            INCIDENTS_TOTAL.labels(
                severity=data.severity.value,
                source=data.source,
            ).inc()

            return self._to_domain(incident_model)

    async def get_incident(self, incident_id: UUID) -> Incident:
        """Get an incident by ID.

        Args:
            incident_id: The incident UUID

        Returns:
            The incident

        Raises:
            IncidentNotFoundError: If incident doesn't exist
        """
        async with get_session() as session:
            repo = IncidentRepository(session)
            incident_model = await repo.get_by_id(incident_id)

            if not incident_model:
                raise IncidentNotFoundError(incident_id)

            return self._to_domain(incident_model)

    async def list_incidents(
        self,
        *,
        status: IncidentStatus | None = None,
        severity: IncidentSeverity | None = None,
        service: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Incident]:
        """List incidents with optional filtering.

        Args:
            status: Filter by status
            severity: Filter by severity
            service: Filter by service name
            source: Filter by source system
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of matching incidents
        """
        async with get_session() as session:
            repo = IncidentRepository(session)
            incident_models = await repo.list_incidents(
                status=status,
                severity=severity,
                service=service,
                source=source,
                limit=limit,
                offset=offset,
            )

            return [self._to_domain(m) for m in incident_models]

    async def update_incident(
        self,
        incident_id: UUID,
        data: IncidentUpdate,
    ) -> Incident:
        """Update an existing incident.

        Args:
            incident_id: The incident UUID
            data: Update data

        Returns:
            The updated incident

        Raises:
            IncidentNotFoundError: If incident doesn't exist
        """
        async with get_session() as session:
            repo = IncidentRepository(session)
            incident_model = await repo.get_by_id(incident_id)

            if not incident_model:
                raise IncidentNotFoundError(incident_id)

            # Apply updates
            update_dict = data.model_dump(exclude_unset=True)
            for field, value in update_dict.items():
                if field == "severity" and value:
                    value = value.value
                elif field == "status" and value:
                    value = value.value
                elif field == "metadata":
                    field = "metadata_"
                setattr(incident_model, field, value)

            # Handle status transitions
            if data.status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
                if not incident_model.resolved_at:
                    incident_model.resolved_at = utc_now()

            incident_model.updated_at = utc_now()
            incident_model = await repo.update(incident_model)

            logger.info(
                "Incident updated",
                incident_id=str(incident_id),
                updates=list(update_dict.keys()),
            )

            return self._to_domain(incident_model)

    async def delete_incident(self, incident_id: UUID) -> bool:
        """Delete an incident.

        Args:
            incident_id: The incident UUID

        Returns:
            True if deleted

        Raises:
            IncidentNotFoundError: If incident doesn't exist
        """
        async with get_session() as session:
            repo = IncidentRepository(session)

            if not await repo.delete_by_id(incident_id):
                raise IncidentNotFoundError(incident_id)

            logger.info("Incident deleted", incident_id=str(incident_id))
            return True

    async def get_incident_stats(self) -> dict:
        """Get incident statistics.

        Returns:
            Dictionary with counts by status and severity
        """
        async with get_session() as session:
            repo = IncidentRepository(session)

            status_counts = await repo.count_by_status()
            severity_counts = await repo.count_by_severity()

            # Update active incidents gauge
            active_count = sum(
                count for status, count in status_counts.items()
                if status in ("open", "investigating", "mitigated")
            )

            return {
                "total": sum(status_counts.values()),
                "by_status": status_counts,
                "by_severity": severity_counts,
                "active": active_count,
            }

    def _to_domain(self, model: IncidentModel) -> Incident:
        """Convert ORM model to domain model."""
        return Incident(
            id=model.id,
            external_id=model.external_id,
            title=model.title,
            description=model.description,
            severity=IncidentSeverity(model.severity),
            status=IncidentStatus(model.status),
            source=model.source,
            service=model.service,
            environment=model.environment,
            tags=model.tags,
            metadata=model.metadata_,
            created_at=model.created_at,
            updated_at=model.updated_at,
            resolved_at=model.resolved_at,
        )
