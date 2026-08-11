"""Engine/session construction. One `Engine` per process — call
`create_database_engine` once at startup and share it."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


class DatabaseSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_DATABASE_")

    url: str
    connect_timeout_seconds: int = 5
    pool_size: int = 5
    pool_max_overflow: int = 10


def create_database_engine(settings: DatabaseSettings) -> Engine:
    return create_engine(
        settings.url,
        pool_size=settings.pool_size,
        max_overflow=settings.pool_max_overflow,
        connect_args={"connect_timeout": settings.connect_timeout_seconds},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction per `with` block: commits on success, rolls back on error."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
