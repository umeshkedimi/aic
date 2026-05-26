"""FastAPI dependency injection.

Centralized dependency definitions for use across the application.
"""

from typing import Annotated, AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aic.config import Settings, get_settings
from aic.infrastructure.database.session import get_session
from aic.application.incidents import IncidentService


# Settings dependency
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for database session."""
    async with get_session() as session:
        yield session


# Database session dependency
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_incident_service() -> IncidentService:
    """Dependency for incident service."""
    return IncidentService()


# Incident service dependency
IncidentServiceDep = Annotated[IncidentService, Depends(get_incident_service)]
