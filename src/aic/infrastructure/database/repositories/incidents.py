"""Incident repository for database operations."""

from typing import Sequence
from uuid import UUID

from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession

from aic.infrastructure.database.models import IncidentModel
from aic.infrastructure.database.repositories.base import BaseRepository
from aic.domain.incidents.models import IncidentSeverity, IncidentStatus
from aic.shared import generate_incident_id, utc_now


class IncidentRepository(BaseRepository[IncidentModel]):
    """Repository for incident database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(session, IncidentModel)

    async def get_by_external_id(self, external_id: str) -> IncidentModel | None:
        """Get incident by external ID (from source system)."""
        query = select(IncidentModel).where(IncidentModel.external_id == external_id)
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def list_incidents(
        self,
        *,
        status: IncidentStatus | None = None,
        severity: IncidentSeverity | None = None,
        service: str | None = None,
        source: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[IncidentModel]:
        """List incidents with filtering and pagination."""
        conditions = []

        if status:
            conditions.append(IncidentModel.status == status.value)
        if severity:
            conditions.append(IncidentModel.severity == severity.value)
        if service:
            conditions.append(IncidentModel.service == service)
        if source:
            conditions.append(IncidentModel.source == source)

        query = (
            select(IncidentModel)
            .where(and_(*conditions) if conditions else True)
            .order_by(desc(IncidentModel.created_at))
            .limit(limit)
            .offset(offset)
        )

        result = await self._session.execute(query)
        return result.scalars().all()

    async def list_active_incidents(
        self,
        *,
        severity: IncidentSeverity | None = None,
        limit: int = 50,
    ) -> Sequence[IncidentModel]:
        """List only active (non-resolved) incidents."""
        conditions = [
            IncidentModel.status.in_(
                [IncidentStatus.OPEN.value, IncidentStatus.INVESTIGATING.value, IncidentStatus.MITIGATED.value]
            )
        ]

        if severity:
            conditions.append(IncidentModel.severity == severity.value)

        query = (
            select(IncidentModel)
            .where(and_(*conditions))
            .order_by(desc(IncidentModel.created_at))
            .limit(limit)
        )

        result = await self._session.execute(query)
        return result.scalars().all()

    async def count_by_status(self) -> dict[str, int]:
        """Count incidents grouped by status."""
        from sqlalchemy import func

        query = (
            select(IncidentModel.status, func.count(IncidentModel.id))
            .group_by(IncidentModel.status)
        )
        result = await self._session.execute(query)
        return {row[0]: row[1] for row in result.all()}

    async def count_by_severity(self) -> dict[str, int]:
        """Count incidents grouped by severity."""
        from sqlalchemy import func

        query = (
            select(IncidentModel.severity, func.count(IncidentModel.id))
            .group_by(IncidentModel.severity)
        )
        result = await self._session.execute(query)
        return {row[0]: row[1] for row in result.all()}

    def create_incident_model(
        self,
        *,
        title: str,
        severity: str,
        source: str,
        description: str | None = None,
        external_id: str | None = None,
        service: str | None = None,
        environment: str | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> IncidentModel:
        """Create a new incident model instance (not yet persisted)."""
        now = utc_now()
        return IncidentModel(
            id=generate_incident_id(),
            external_id=external_id,
            title=title,
            description=description,
            severity=severity,
            status=IncidentStatus.OPEN.value,
            source=source,
            service=service,
            environment=environment,
            tags=tags or [],
            metadata_=metadata or {},
            created_at=now,
            updated_at=now,
        )
