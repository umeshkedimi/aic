"""Declarative base shared by every ORM model, with a fixed constraint-naming
convention so Alembic autogenerate produces stable, predictable migration
names instead of dialect-assigned ones that vary run to run."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012
        # Every timestamp in this schema is timezone-aware; naive datetimes
        # are a recurring source of silent bugs once services cross UTC
        # boundaries, so this is enforced once here instead of per-column.
        datetime: DateTime(timezone=True),
    }
