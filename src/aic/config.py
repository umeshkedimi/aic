"""Application configuration management.

Uses Pydantic Settings for type-safe configuration with environment variable support.
All configuration is centralized here and validated at startup.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation and environment loading."""

    model_config = SettingsConfigDict(
        env_prefix="AIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # =========================================================================
    # Application
    # =========================================================================
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    service_name: str = "aic"

    # =========================================================================
    # Server
    # =========================================================================
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1

    # =========================================================================
    # Database (PostgreSQL)
    # =========================================================================
    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://aic:aic_dev_password@localhost:5432/aic"
    )
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_echo: bool = False

    # =========================================================================
    # Cache (Redis)
    # =========================================================================
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = 10
    redis_socket_timeout: float = 5.0

    # =========================================================================
    # Vector Store (Qdrant)
    # =========================================================================
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "aic_knowledge"
    qdrant_vector_size: int = 1536  # OpenAI text-embedding-3-small

    # =========================================================================
    # LLM Configuration
    # =========================================================================
    llm_provider: Literal["openai", "azure"] = "openai"

    # OpenAI
    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = "gpt-4-turbo-preview"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_max_tokens: int = 4096
    openai_temperature: float = 0.1
    openai_request_timeout: float = 60.0

    # Azure OpenAI (alternative)
    azure_openai_api_key: str = Field(default="", repr=False)
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"

    # =========================================================================
    # Observability
    # =========================================================================
    otlp_endpoint: str = "http://localhost:4317"
    otlp_enabled: bool = True
    metrics_enabled: bool = True
    metrics_port: int = 8001

    # =========================================================================
    # RAG Configuration
    # =========================================================================
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5
    rag_score_threshold: float = 0.7

    # =========================================================================
    # Security
    # =========================================================================
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    api_key_header: str = "X-API-Key"
    api_keys: list[str] = Field(default_factory=list, repr=False)

    # =========================================================================
    # Validators
    # =========================================================================
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    # =========================================================================
    # Computed Properties
    # =========================================================================
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def database_url_sync(self) -> str:
        """Sync database URL for Alembic migrations."""
        return str(self.database_url).replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Using lru_cache ensures settings are loaded once and reused,
    avoiding repeated environment variable parsing.
    """
    return Settings()
