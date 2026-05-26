"""Base repository pattern for database access.

Provides common CRUD operations with async SQLAlchemy.
"""

from typing import Generic, TypeVar, Type, Sequence
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from aic.infrastructure.database.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Base repository with common CRUD operations."""

    def __init__(self, session: AsyncSession, model_class: Type[ModelT]):
        self._session = session
        self._model_class = model_class

    async def get_by_id(self, id: UUID) -> ModelT | None:
        """Get a single entity by ID."""
        return await self._session.get(self._model_class, id)

    async def get_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ModelT]:
        """Get all entities with pagination."""
        query = select(self._model_class).limit(limit).offset(offset)
        result = await self._session.execute(query)
        return result.scalars().all()

    async def count(self) -> int:
        """Count total entities."""
        query = select(func.count()).select_from(self._model_class)
        result = await self._session.execute(query)
        return result.scalar() or 0

    async def create(self, entity: ModelT) -> ModelT:
        """Create a new entity."""
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def update(self, entity: ModelT) -> ModelT:
        """Update an existing entity."""
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        """Delete an entity."""
        await self._session.delete(entity)
        await self._session.flush()

    async def delete_by_id(self, id: UUID) -> bool:
        """Delete an entity by ID. Returns True if deleted."""
        entity = await self.get_by_id(id)
        if entity:
            await self.delete(entity)
            return True
        return False
