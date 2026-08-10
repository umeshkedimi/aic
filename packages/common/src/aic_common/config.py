"""Shared settings base. Each service extends this with its own fields."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "aic"
    environment: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://aic:aic@localhost:5432/aic"
    redis_url: str = "redis://localhost:6379/0"

    otel_exporter_otlp_endpoint: str | None = None
