"""Base settings every AIC package/service config should build on."""

from __future__ import annotations

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PROD = "prod"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class AICBaseSettings(BaseSettings):
    """Base class for a service's settings.

    Subclass with an explicit ``env_prefix`` (e.g. ``AIC_INGEST_``) per
    service so env vars for different services can never collide::

        class IngestSettings(AICBaseSettings):
            model_config = SettingsConfigDict(env_prefix="AIC_INGEST_")
            kafka_bootstrap_servers: str
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.LOCAL
    log_level: LogLevel = LogLevel.INFO
