"""Incident API endpoints.

RESTful API for incident management including CRUD operations
and filtering.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from pydantic import BaseModel, Field

from aic.domain.incidents import (
    Incident,
    IncidentCreate,
    IncidentUpdate,
    IncidentSeverity,
    IncidentStatus,
    IncidentNotFoundError,
)
from aic.domain.incidents.exceptions import DuplicateIncidentError
from aic.application.incidents import IncidentService
from aic.observability.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class IncidentResponse(BaseModel):
    """API response model for a single incident."""

    id: UUID
    external_id: str | None
    title: str
    description: str | None
    severity: IncidentSeverity
    status: IncidentStatus
    source: str
    service: str | None
    environment: str | None
    tags: list[str]
    created_at: str
    updated_at: str
    resolved_at: str | None
    is_active: bool

    @classmethod
    def from_domain(cls, incident: Incident) -> "IncidentResponse":
        return cls(
            id=incident.id,
            external_id=incident.external_id,
            title=incident.title,
            description=incident.description,
            severity=incident.severity,
            status=incident.status,
            source=incident.source,
            service=incident.service,
            environment=incident.environment,
            tags=incident.tags,
            created_at=incident.created_at.isoformat(),
            updated_at=incident.updated_at.isoformat(),
            resolved_at=incident.resolved_at.isoformat() if incident.resolved_at else None,
            is_active=incident.is_active,
        )


class IncidentListResponse(BaseModel):
    """API response model for incident list."""

    incidents: list[IncidentResponse]
    total: int
    limit: int
    offset: int


class IncidentStatsResponse(BaseModel):
    """API response model for incident statistics."""

    total: int
    active: int
    by_status: dict[str, int]
    by_severity: dict[str, int]


class CreateIncidentRequest(BaseModel):
    """API request model for creating an incident."""

    external_id: str | None = Field(default=None, max_length=255)
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    severity: IncidentSeverity
    source: str = Field(..., min_length=1, max_length=100)
    service: str | None = Field(default=None, max_length=255)
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class UpdateIncidentRequest(BaseModel):
    """API request model for updating an incident."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    severity: IncidentSeverity | None = None
    status: IncidentStatus | None = None
    service: str | None = Field(default=None, max_length=255)
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = None
    metadata: dict | None = None


# =============================================================================
# Dependencies
# =============================================================================


def get_incident_service() -> IncidentService:
    """Dependency to get incident service."""
    return IncidentService()


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new incident",
)
async def create_incident(
    request: CreateIncidentRequest,
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> IncidentResponse:
    """Create a new incident.

    - **external_id**: Optional ID from the source system (must be unique)
    - **title**: Short description of the incident
    - **severity**: Incident severity (critical, high, medium, low, info)
    - **source**: Source system (e.g., alertmanager, pagerduty)
    - **service**: Affected service name
    - **tags**: List of tags for categorization
    """
    try:
        incident = await service.create_incident(
            IncidentCreate(
                external_id=request.external_id,
                title=request.title,
                description=request.description,
                severity=request.severity,
                source=request.source,
                service=request.service,
                environment=request.environment,
                tags=request.tags,
                metadata=request.metadata,
            )
        )
        return IncidentResponse.from_domain(incident)
    except DuplicateIncidentError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@router.get(
    "",
    response_model=IncidentListResponse,
    summary="List incidents",
)
async def list_incidents(
    service: Annotated[IncidentService, Depends(get_incident_service)],
    status_filter: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    severity: IncidentSeverity | None = None,
    service_name: Annotated[str | None, Query(alias="service")] = None,
    source: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> IncidentListResponse:
    """List incidents with optional filtering.

    - **status**: Filter by incident status
    - **severity**: Filter by severity level
    - **service**: Filter by affected service
    - **source**: Filter by source system
    """
    incidents = await service.list_incidents(
        status=status_filter,
        severity=severity,
        service=service_name,
        source=source,
        limit=limit,
        offset=offset,
    )

    return IncidentListResponse(
        incidents=[IncidentResponse.from_domain(i) for i in incidents],
        total=len(incidents),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/stats",
    response_model=IncidentStatsResponse,
    summary="Get incident statistics",
)
async def get_incident_stats(
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> IncidentStatsResponse:
    """Get incident statistics including counts by status and severity."""
    stats = await service.get_incident_stats()
    return IncidentStatsResponse(**stats)


@router.get(
    "/{incident_id}",
    response_model=IncidentResponse,
    summary="Get an incident",
)
async def get_incident(
    incident_id: UUID,
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> IncidentResponse:
    """Get a single incident by ID."""
    try:
        incident = await service.get_incident(incident_id)
        return IncidentResponse.from_domain(incident)
    except IncidentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident not found: {incident_id}",
        )


@router.patch(
    "/{incident_id}",
    response_model=IncidentResponse,
    summary="Update an incident",
)
async def update_incident(
    incident_id: UUID,
    request: UpdateIncidentRequest,
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> IncidentResponse:
    """Update an existing incident.

    Only provided fields will be updated.
    """
    try:
        incident = await service.update_incident(
            incident_id,
            IncidentUpdate(
                title=request.title,
                description=request.description,
                severity=request.severity,
                status=request.status,
                service=request.service,
                environment=request.environment,
                tags=request.tags,
                metadata=request.metadata,
            ),
        )
        return IncidentResponse.from_domain(incident)
    except IncidentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident not found: {incident_id}",
        )


@router.delete(
    "/{incident_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an incident",
)
async def delete_incident(
    incident_id: UUID,
    service: Annotated[IncidentService, Depends(get_incident_service)],
) -> None:
    """Delete an incident by ID."""
    try:
        await service.delete_incident(incident_id)
    except IncidentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident not found: {incident_id}",
        )
