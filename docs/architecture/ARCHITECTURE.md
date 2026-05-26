# AIC (AI Incident Commander) - System Architecture

## Executive Summary

AIC is an enterprise-grade AI-powered operational intelligence platform designed to reduce Mean Time To Resolution (MTTR) for platform and SRE teams. The system ingests operational incidents, performs AI-driven root cause analysis, retrieves relevant operational knowledge via RAG, and orchestrates investigation workflows.

This document defines the complete system architecture, serving as the authoritative reference for all implementation decisions.

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                   CLIENTS                                        │
│                    (Alertmanager, PagerDuty, Custom Integrations)               │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              API GATEWAY LAYER                                   │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         FastAPI Application                              │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │   │
│  │  │  Incidents   │  │   Health     │  │  Knowledge   │  │   Admin     │  │   │
│  │  │   Router     │  │   Router     │  │   Router     │  │   Router    │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
┌──────────────────────────┐ ┌─────────────────┐ ┌─────────────────────────────┐
│    APPLICATION LAYER     │ │  DOMAIN LAYER   │ │    INFRASTRUCTURE LAYER     │
│  ┌────────────────────┐  │ │ ┌─────────────┐ │ │  ┌───────────────────────┐  │
│  │  Incident Service  │  │ │ │  Incident   │ │ │  │   PostgreSQL Repo     │  │
│  │  Analysis Service  │  │ │ │  Analysis   │ │ │  │   Redis Cache         │  │
│  │  Knowledge Service │  │ │ │  Knowledge  │ │ │  │   Qdrant Vector Store │  │
│  │  Agent Service     │  │ │ │  Agent      │ │ │  │   LLM Clients         │  │
│  └────────────────────┘  │ │ └─────────────┘ │ │  └───────────────────────┘  │
└──────────────────────────┘ └─────────────────┘ └─────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           AI INTELLIGENCE LAYER                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────────┐  │
│  │   RCA Engine    │  │  RAG Pipeline   │  │     Agent Orchestrator          │  │
│  │  (Structured    │  │  (Embedding +   │  │     (LangGraph Workflows)       │  │
│  │   Outputs)      │  │   Retrieval)    │  │                                 │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           OBSERVABILITY LAYER                                    │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌───────────┐  │
│  │ OpenTelemetry   │  │   Structured    │  │   Prometheus    │  │  Grafana  │  │
│  │    Tracing      │  │    Logging      │  │    Metrics      │  │ Dashboards│  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  └───────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           PERSISTENCE LAYER                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────────┐  │
│  │   PostgreSQL    │  │     Redis       │  │           Qdrant                │  │
│  │  (Primary DB)   │  │  (Cache/Queue)  │  │      (Vector Store)             │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Architecture Principles

1. **Clean Architecture**: Domain logic is isolated from infrastructure concerns
2. **Dependency Inversion**: High-level modules don't depend on low-level modules
3. **Async-First**: All I/O operations are asynchronous
4. **Modular Monolith**: Single deployable unit with clear module boundaries
5. **Observable by Default**: Every operation is traced and metered
6. **Fail-Safe**: Graceful degradation when AI services are unavailable

---

## 2. Repository Structure

```
aic/
├── docs/
│   ├── architecture/
│   │   └── ARCHITECTURE.md          # This document
│   ├── api/
│   │   └── openapi.yaml             # API specification
│   └── runbooks/                    # Operational runbooks
│
├── src/
│   └── aic/
│       ├── __init__.py
│       ├── main.py                  # Application entrypoint
│       ├── config.py                # Configuration management
│       ├── dependencies.py          # Dependency injection
│       │
│       ├── api/                     # API Layer
│       │   ├── __init__.py
│       │   ├── app.py               # FastAPI app factory
│       │   ├── middleware/
│       │   │   ├── __init__.py
│       │   │   ├── correlation.py   # Request correlation IDs
│       │   │   ├── error_handler.py # Global error handling
│       │   │   └── timing.py        # Request timing
│       │   └── v1/
│       │       ├── __init__.py
│       │       ├── router.py        # v1 API router
│       │       ├── incidents.py     # Incident endpoints
│       │       ├── knowledge.py     # Knowledge base endpoints
│       │       ├── analysis.py      # Analysis endpoints
│       │       └── health.py        # Health check endpoints
│       │
│       ├── domain/                  # Domain Layer (Pure Business Logic)
│       │   ├── __init__.py
│       │   ├── incidents/
│       │   │   ├── __init__.py
│       │   │   ├── models.py        # Incident domain models
│       │   │   ├── events.py        # Domain events
│       │   │   └── exceptions.py    # Domain exceptions
│       │   ├── analysis/
│       │   │   ├── __init__.py
│       │   │   ├── models.py        # Analysis domain models
│       │   │   └── exceptions.py
│       │   ├── knowledge/
│       │   │   ├── __init__.py
│       │   │   ├── models.py        # Knowledge domain models
│       │   │   └── exceptions.py
│       │   └── agents/
│       │       ├── __init__.py
│       │       ├── models.py        # Agent domain models
│       │       └── exceptions.py
│       │
│       ├── application/             # Application Layer (Use Cases)
│       │   ├── __init__.py
│       │   ├── incidents/
│       │   │   ├── __init__.py
│       │   │   ├── service.py       # Incident service
│       │   │   ├── commands.py      # Command DTOs
│       │   │   └── queries.py       # Query DTOs
│       │   ├── analysis/
│       │   │   ├── __init__.py
│       │   │   ├── service.py       # Analysis service
│       │   │   └── commands.py
│       │   ├── knowledge/
│       │   │   ├── __init__.py
│       │   │   ├── service.py       # Knowledge service
│       │   │   └── commands.py
│       │   └── agents/
│       │       ├── __init__.py
│       │       ├── service.py       # Agent orchestration service
│       │       └── commands.py
│       │
│       ├── infrastructure/          # Infrastructure Layer
│       │   ├── __init__.py
│       │   ├── database/
│       │   │   ├── __init__.py
│       │   │   ├── session.py       # Async session management
│       │   │   ├── models.py        # SQLAlchemy ORM models
│       │   │   └── repositories/
│       │   │       ├── __init__.py
│       │   │       ├── base.py      # Base repository
│       │   │       ├── incidents.py # Incident repository
│       │   │       └── knowledge.py # Knowledge repository
│       │   ├── cache/
│       │   │   ├── __init__.py
│       │   │   └── redis.py         # Redis cache client
│       │   ├── vector/
│       │   │   ├── __init__.py
│       │   │   └── qdrant.py        # Qdrant vector store
│       │   └── external/
│       │       ├── __init__.py
│       │       └── llm/
│       │           ├── __init__.py
│       │           ├── base.py      # LLM client interface
│       │           ├── openai.py    # OpenAI implementation
│       │           └── azure.py     # Azure OpenAI implementation
│       │
│       ├── ai/                      # AI Intelligence Layer
│       │   ├── __init__.py
│       │   ├── rca/
│       │   │   ├── __init__.py
│       │   │   ├── engine.py        # RCA engine
│       │   │   ├── prompts.py       # Prompt templates
│       │   │   └── schemas.py       # Structured output schemas
│       │   ├── rag/
│       │   │   ├── __init__.py
│       │   │   ├── pipeline.py      # RAG pipeline
│       │   │   ├── embeddings.py    # Embedding generation
│       │   │   ├── retriever.py     # Semantic retrieval
│       │   │   └── chunker.py       # Document chunking
│       │   └── agents/
│       │       ├── __init__.py
│       │       ├── orchestrator.py  # LangGraph orchestrator
│       │       ├── graphs/
│       │       │   ├── __init__.py
│       │       │   ├── rca_graph.py # RCA agent graph
│       │       │   └── triage_graph.py
│       │       └── tools/
│       │           ├── __init__.py
│       │           ├── log_analysis.py
│       │           └── runbook_lookup.py
│       │
│       ├── observability/           # Observability Layer
│       │   ├── __init__.py
│       │   ├── logging.py           # Structured logging setup
│       │   ├── tracing.py           # OpenTelemetry setup
│       │   └── metrics.py           # Prometheus metrics
│       │
│       └── shared/                  # Shared Utilities
│           ├── __init__.py
│           ├── types.py             # Common type definitions
│           ├── time.py              # Time utilities
│           └── identifiers.py       # ID generation
│
├── migrations/                      # Alembic migrations
│   ├── env.py
│   ├── alembic.ini
│   └── versions/
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # Pytest fixtures
│   ├── unit/
│   │   ├── domain/
│   │   ├── application/
│   │   └── ai/
│   ├── integration/
│   │   ├── api/
│   │   ├── database/
│   │   └── external/
│   └── e2e/
│
├── scripts/
│   ├── seed_knowledge.py            # Seed knowledge base
│   └── generate_test_incidents.py   # Generate test data
│
├── docker/
│   ├── Dockerfile                   # Production Dockerfile
│   ├── Dockerfile.dev               # Development Dockerfile
│   └── entrypoint.sh
│
├── docker-compose.yml               # Local development stack
├── docker-compose.prod.yml          # Production stack
├── pyproject.toml                   # Project configuration
├── .env.example                     # Environment template
├── .gitignore
├── Makefile                         # Developer commands
└── README.md
```

---

## 3. Service Boundaries and Modules

### Module Responsibilities

| Module | Responsibility | Dependencies |
|--------|---------------|--------------|
| **api** | HTTP interface, request validation, response formatting | application |
| **domain** | Pure business logic, entities, value objects, domain events | None (isolated) |
| **application** | Use case orchestration, transaction management | domain, infrastructure interfaces |
| **infrastructure** | External system integrations, persistence | domain (for mapping) |
| **ai** | AI/ML capabilities, LLM interactions, RAG | infrastructure.llm, infrastructure.vector |
| **observability** | Cross-cutting telemetry concerns | None |
| **shared** | Common utilities used across modules | None |

### Dependency Rules

```
api → application → domain
         ↓
   infrastructure (implements domain interfaces)
         ↓
   ai (uses infrastructure for LLM/vector access)
```

**Critical Rule**: Domain layer has ZERO external dependencies. It defines interfaces that infrastructure implements.

---

## 4. Database Schema Design

### Core Tables

```sql
-- Incidents table
CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id VARCHAR(255) UNIQUE,          -- ID from source system
    title VARCHAR(500) NOT NULL,
    description TEXT,
    severity VARCHAR(20) NOT NULL,            -- critical, high, medium, low, info
    status VARCHAR(50) NOT NULL DEFAULT 'open', -- open, investigating, mitigated, resolved, closed
    source VARCHAR(100) NOT NULL,             -- alertmanager, pagerduty, manual, etc.
    service VARCHAR(255),                     -- Affected service
    environment VARCHAR(50),                  -- prod, staging, dev
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',              -- Flexible metadata storage
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    
    -- Indexes
    CONSTRAINT valid_severity CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    CONSTRAINT valid_status CHECK (status IN ('open', 'investigating', 'mitigated', 'resolved', 'closed'))
);

CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_severity ON incidents(severity);
CREATE INDEX idx_incidents_service ON incidents(service);
CREATE INDEX idx_incidents_created_at ON incidents(created_at DESC);
CREATE INDEX idx_incidents_source ON incidents(source);
CREATE INDEX idx_incidents_tags ON incidents USING GIN(tags);

-- Incident analyses table (AI-generated)
CREATE TABLE incident_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    analysis_type VARCHAR(50) NOT NULL,       -- rca, summary, triage
    summary TEXT,
    root_cause_hypothesis TEXT,
    confidence_score DECIMAL(3,2),            -- 0.00 to 1.00
    severity_assessment VARCHAR(20),
    suggested_actions JSONB DEFAULT '[]',
    related_incidents JSONB DEFAULT '[]',     -- UUIDs of related incidents
    context_used JSONB DEFAULT '{}',          -- RAG context metadata
    model_used VARCHAR(100),
    tokens_used INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT valid_confidence CHECK (confidence_score >= 0 AND confidence_score <= 1)
);

CREATE INDEX idx_analyses_incident ON incident_analyses(incident_id);
CREATE INDEX idx_analyses_type ON incident_analyses(analysis_type);

-- Knowledge documents table
CREATE TABLE knowledge_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    doc_type VARCHAR(50) NOT NULL,            -- runbook, sop, architecture, postmortem
    service VARCHAR(255),
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    version INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(255)
);

CREATE INDEX idx_knowledge_type ON knowledge_documents(doc_type);
CREATE INDEX idx_knowledge_service ON knowledge_documents(service);
CREATE INDEX idx_knowledge_active ON knowledge_documents(is_active);
CREATE INDEX idx_knowledge_tags ON knowledge_documents USING GIN(tags);

-- Knowledge chunks table (for RAG)
CREATE TABLE knowledge_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER,
    embedding_id VARCHAR(255),                -- Reference to vector store
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chunks_document ON knowledge_chunks(document_id);
CREATE UNIQUE INDEX idx_chunks_doc_index ON knowledge_chunks(document_id, chunk_index);

-- Incident timeline events
CREATE TABLE incident_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,          -- created, updated, analysis_completed, escalated, etc.
    actor VARCHAR(255),                       -- user or system
    description TEXT,
    data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_incident ON incident_events(incident_id);
CREATE INDEX idx_events_type ON incident_events(event_type);
CREATE INDEX idx_events_created ON incident_events(created_at DESC);

-- Agent executions table (for future agent orchestration)
CREATE TABLE agent_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    agent_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    input_data JSONB,
    output_data JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agent_exec_incident ON agent_executions(incident_id);
CREATE INDEX idx_agent_exec_status ON agent_executions(status);
```

### Entity Relationship Diagram

```
┌─────────────────┐       ┌─────────────────────┐
│    incidents    │───1:N─│  incident_analyses  │
└────────┬────────┘       └─────────────────────┘
         │
         │1:N
         ▼
┌─────────────────┐
│ incident_events │
└─────────────────┘

┌─────────────────────┐       ┌──────────────────┐
│ knowledge_documents │───1:N─│ knowledge_chunks │
└─────────────────────┘       └──────────────────┘

┌─────────────────┐
│agent_executions │──────────(optional FK to incidents)
└─────────────────┘
```

---

## 5. Docker Compose Setup

```yaml
# docker-compose.yml
version: '3.8'

services:
  api:
    build:
      context: .
      dockerfile: docker/Dockerfile.dev
    ports:
      - "8000:8000"
    environment:
      - AIC_ENV=development
      - AIC_DEBUG=true
      - AIC_DATABASE_URL=postgresql+asyncpg://aic:aic_dev_password@postgres:5432/aic
      - AIC_REDIS_URL=redis://redis:6379/0
      - AIC_QDRANT_HOST=qdrant
      - AIC_QDRANT_PORT=6333
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - ./src:/app/src
      - ./tests:/app/tests
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      qdrant:
        condition: service_started
    networks:
      - aic-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: aic
      POSTGRES_USER: aic
      POSTGRES_PASSWORD: aic_dev_password
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - aic-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aic -d aic"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    networks:
      - aic-network
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant_data:/qdrant/storage
    networks:
      - aic-network

  # Observability Stack
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./docker/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    networks:
      - aic-network

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana_data:/var/lib/grafana
      - ./docker/grafana/provisioning:/etc/grafana/provisioning
    depends_on:
      - prometheus
    networks:
      - aic-network

  jaeger:
    image: jaegertracing/all-in-one:latest
    ports:
      - "16686:16686"  # UI
      - "4317:4317"    # OTLP gRPC
      - "4318:4318"    # OTLP HTTP
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    networks:
      - aic-network

volumes:
  postgres_data:
  redis_data:
  qdrant_data:
  prometheus_data:
  grafana_data:

networks:
  aic-network:
    driver: bridge
```

---

## 6. FastAPI Application Structure

### Application Factory Pattern

```python
# src/aic/api/app.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aic.config import Settings
from aic.api.middleware.correlation import CorrelationMiddleware
from aic.api.middleware.error_handler import error_handler_middleware
from aic.api.middleware.timing import TimingMiddleware
from aic.api.v1.router import api_v1_router
from aic.observability.logging import setup_logging
from aic.observability.tracing import setup_tracing
from aic.observability.metrics import setup_metrics
from aic.infrastructure.database.session import init_db, close_db
from aic.infrastructure.cache.redis import init_redis, close_redis
from aic.infrastructure.vector.qdrant import init_qdrant, close_qdrant


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown."""
    settings = app.state.settings
    
    # Startup
    setup_logging(settings)
    setup_tracing(settings)
    setup_metrics(settings)
    
    await init_db(settings)
    await init_redis(settings)
    await init_qdrant(settings)
    
    yield
    
    # Shutdown
    await close_qdrant()
    await close_redis()
    await close_db()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Factory function to create FastAPI application."""
    if settings is None:
        settings = Settings()
    
    app = FastAPI(
        title="AIC - AI Incident Commander",
        description="AI-powered operational intelligence platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )
    
    app.state.settings = settings
    
    # Middleware (order matters - first added is outermost)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Exception handlers
    app.middleware("http")(error_handler_middleware)
    
    # Routers
    app.include_router(api_v1_router, prefix="/api/v1")
    
    return app
```

### API Router Structure

```python
# src/aic/api/v1/router.py
from fastapi import APIRouter

from aic.api.v1.incidents import router as incidents_router
from aic.api.v1.analysis import router as analysis_router
from aic.api.v1.knowledge import router as knowledge_router
from aic.api.v1.health import router as health_router

api_v1_router = APIRouter()

api_v1_router.include_router(health_router, tags=["Health"])
api_v1_router.include_router(incidents_router, prefix="/incidents", tags=["Incidents"])
api_v1_router.include_router(analysis_router, prefix="/analysis", tags=["Analysis"])
api_v1_router.include_router(knowledge_router, prefix="/knowledge", tags=["Knowledge"])
```

---

## 7. Configuration Management

### Pydantic Settings with Validation

```python
# src/aic/config.py
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation and environment loading."""
    
    model_config = SettingsConfigDict(
        env_prefix="AIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
    
    # Application
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    
    # Database
    database_url: PostgresDsn
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    
    # Redis
    redis_url: RedisDsn
    redis_max_connections: int = 10
    
    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "aic_knowledge"
    
    # OpenAI
    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = "gpt-4-turbo-preview"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_max_tokens: int = 4096
    openai_temperature: float = 0.1
    
    # Azure OpenAI (alternative)
    azure_openai_api_key: str = Field(default="", repr=False)
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"
    
    # LLM Provider Selection
    llm_provider: Literal["openai", "azure"] = "openai"
    
    # Observability
    otlp_endpoint: str = "http://localhost:4317"
    otlp_enabled: bool = True
    metrics_enabled: bool = True
    
    # Security
    cors_origins: list[str] = ["*"]
    api_key_header: str = "X-API-Key"
    
    # RAG
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5
    
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v
    
    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
```

---

## 8. Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- [ ] Project scaffolding and repository structure
- [ ] Docker Compose environment
- [ ] Configuration management
- [ ] Database setup with Alembic migrations
- [ ] Basic FastAPI application with health endpoints
- [ ] Structured logging and request tracing
- [ ] CI/CD pipeline setup

### Phase 2: Core Incident Management (Week 3-4)
- [ ] Incident domain models and DTOs
- [ ] Incident repository with async SQLAlchemy
- [ ] Incident CRUD API endpoints
- [ ] Incident status workflow
- [ ] Redis caching for hot data
- [ ] API integration tests

### Phase 3: AI Intelligence Layer (Week 5-6)
- [ ] LLM client abstraction (OpenAI/Azure)
- [ ] Structured output schemas with Instructor
- [ ] RCA engine with prompt templates
- [ ] Incident summarization
- [ ] Root cause hypothesis generation
- [ ] Confidence scoring

### Phase 4: RAG Pipeline (Week 7-8)
- [ ] Qdrant vector store integration
- [ ] Document chunking strategy
- [ ] Embedding generation pipeline
- [ ] Semantic retrieval
- [ ] Knowledge ingestion API
- [ ] Context injection for RCA

### Phase 5: Observability (Week 9)
- [ ] OpenTelemetry tracing integration
- [ ] Prometheus metrics
- [ ] Grafana dashboards
- [ ] AI operation tracing (tokens, latency)
- [ ] Alerting rules

### Phase 6: Agent Foundation (Week 10)
- [ ] LangGraph integration
- [ ] Basic agent orchestrator
- [ ] RCA agent graph
- [ ] Tool abstractions
- [ ] Agent execution tracking

---

## 9. Step-by-Step Development Plan

### Step 1: Project Bootstrap
```bash
# Create project structure
mkdir -p src/aic/{api,domain,application,infrastructure,ai,observability,shared}
mkdir -p tests/{unit,integration,e2e}
mkdir -p docker migrations/versions docs/architecture scripts

# Initialize Python project
uv init  # or poetry init
```

### Step 2: Core Dependencies
```toml
# pyproject.toml - key dependencies
[project]
dependencies = [
    # Web framework
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.2.0",
    
    # Database
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
    
    # Cache
    "redis>=5.0.0",
    
    # Vector store
    "qdrant-client>=1.9.0",
    
    # AI/LLM
    "openai>=1.30.0",
    "instructor>=1.2.0",
    "langchain-core>=0.2.0",
    "langgraph>=0.0.50",
    "tiktoken>=0.7.0",
    
    # Observability
    "opentelemetry-api>=1.24.0",
    "opentelemetry-sdk>=1.24.0",
    "opentelemetry-instrumentation-fastapi>=0.45b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.45b0",
    "opentelemetry-exporter-otlp>=1.24.0",
    "prometheus-client>=0.20.0",
    "structlog>=24.1.0",
    
    # Utilities
    "httpx>=0.27.0",
    "python-ulid>=2.2.0",
]
```

### Step 3: Implementation Order
1. `config.py` - Configuration management
2. `shared/` - Common utilities
3. `observability/` - Logging, tracing, metrics
4. `api/app.py` - FastAPI application factory
5. `infrastructure/database/` - Database setup
6. `domain/incidents/` - Incident domain models
7. `infrastructure/database/repositories/` - Repositories
8. `application/incidents/` - Incident service
9. `api/v1/incidents.py` - Incident endpoints
10. `infrastructure/external/llm/` - LLM clients
11. `ai/rca/` - RCA engine
12. `infrastructure/vector/` - Qdrant integration
13. `ai/rag/` - RAG pipeline
14. `ai/agents/` - Agent orchestration

---

## 10. Coding Standards

### Python Standards
- **Type hints**: Required on all function signatures
- **Async**: Use `async def` for all I/O operations
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Imports**: Absolute imports, sorted with isort
- **Docstrings**: Google style for public APIs only
- **Line length**: 100 characters max
- **Error handling**: Use custom exception hierarchy

### Code Quality Tools
```toml
# pyproject.toml
[tool.ruff]
target-version = "py312"
line-length = 100
select = [
    "E", "F", "W",  # pyflakes, pycodestyle
    "I",            # isort
    "N",            # pep8-naming
    "UP",           # pyupgrade
    "B",            # flake8-bugbear
    "C4",           # flake8-comprehensions
    "SIM",          # flake8-simplify
    "TCH",          # flake8-type-checking
    "RUF",          # ruff-specific
]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]

[tool.black]
line-length = 100
target-version = ["py312"]
```

### Git Conventions
- **Commits**: Conventional commits (feat:, fix:, docs:, refactor:, test:, chore:)
- **Branches**: feature/, bugfix/, hotfix/, release/
- **PRs**: Require tests, passing CI, code review

---

## 11. Async Processing Strategy

### Principles
1. **Non-blocking I/O**: All database, cache, HTTP, and LLM calls are async
2. **Connection pooling**: Reuse connections for efficiency
3. **Graceful degradation**: Timeouts and fallbacks for external services
4. **Background tasks**: Use FastAPI BackgroundTasks for fire-and-forget operations

### Async Patterns

```python
# Database session context manager
async with get_session() as session:
    result = await session.execute(query)

# Parallel LLM calls when independent
async def analyze_incident(incident: Incident) -> Analysis:
    summary_task = asyncio.create_task(generate_summary(incident))
    rca_task = asyncio.create_task(generate_rca(incident))
    
    summary, rca = await asyncio.gather(summary_task, rca_task)
    return Analysis(summary=summary, root_cause=rca)

# Timeout wrapper for external calls
async def call_with_timeout(coro, timeout: float = 30.0):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("External call timed out")
        raise ServiceTimeoutError()
```

### Background Processing (Future)
- Redis-based task queue for long-running operations
- Worker processes for batch analysis
- Event-driven processing for incident updates

---

## 12. Observability Strategy

### Three Pillars

#### 1. Structured Logging
```python
# Every log entry includes:
{
    "timestamp": "2024-01-15T10:30:00Z",
    "level": "INFO",
    "message": "Incident analyzed",
    "correlation_id": "01HN...",
    "incident_id": "inc_01HN...",
    "service": "aic",
    "duration_ms": 1234,
    "extra": {...}
}
```

#### 2. Distributed Tracing
- Trace every request end-to-end
- Span for each major operation (DB query, LLM call, RAG retrieval)
- Propagate trace context across async boundaries
- Export to Jaeger/Tempo

#### 3. Metrics
```python
# Key metrics to track:
- request_duration_seconds (histogram)
- request_total (counter, by status_code, endpoint)
- incidents_total (counter, by severity, status)
- llm_request_duration_seconds (histogram, by model)
- llm_tokens_total (counter, by type: input/output)
- rag_retrieval_duration_seconds (histogram)
- db_query_duration_seconds (histogram)
- active_incidents_gauge (gauge)
```

### AI-Specific Observability
- Track token usage per request
- Measure LLM latency percentiles
- Log prompt/response pairs (sanitized) for debugging
- Monitor embedding generation throughput
- Alert on confidence score degradation

---

## 13. RAG Pipeline Design

```
┌─────────────────────────────────────────────────────────────────┐
│                      RAG PIPELINE                                │
└─────────────────────────────────────────────────────────────────┘

INGESTION FLOW:
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Document    │───▶│   Chunker    │───▶│  Embedder    │───▶│   Qdrant     │
│  Upload      │    │  (512 tok)   │    │  (OpenAI)    │    │  (Store)     │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                           │                    │
                           ▼                    ▼
                    ┌──────────────┐    ┌──────────────┐
                    │  PostgreSQL  │    │   Metadata   │
                    │  (Chunks)    │    │   Index      │
                    └──────────────┘    └──────────────┘

RETRIEVAL FLOW:
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Incident    │───▶│   Query      │───▶│  Semantic    │───▶│   Re-rank    │
│  Context     │    │  Embedding   │    │  Search      │    │  (Optional)  │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                                                                    │
                                                                    ▼
                                                            ┌──────────────┐
                                                            │   Context    │
                                                            │   Assembly   │
                                                            └──────────────┘
```

### Chunking Strategy
- **Chunk size**: 512 tokens (balances context and precision)
- **Overlap**: 50 tokens (maintains context continuity)
- **Metadata**: Preserve source, document type, service tags
- **Separators**: Respect markdown headers, code blocks

### Retrieval Strategy
- **Top-K**: 5 chunks default (configurable)
- **Score threshold**: Filter low-relevance results
- **Metadata filtering**: Filter by service, doc_type when known
- **Hybrid search**: Combine semantic + keyword (future)

---

## 14. AI RCA Workflow Design

```
┌─────────────────────────────────────────────────────────────────┐
│                    RCA ENGINE WORKFLOW                           │
└─────────────────────────────────────────────────────────────────┘

INPUT:
┌──────────────────────────────────────────────────────────────────┐
│ Incident:                                                         │
│   - title, description, severity                                  │
│   - service, environment                                          │
│   - timeline, error messages                                      │
│   - affected components                                           │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 1: Context Gathering                                         │
│   ├─ RAG: Retrieve relevant runbooks                             │
│   ├─ RAG: Retrieve similar past incidents                        │
│   └─ RAG: Retrieve architecture docs for service                 │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 2: Analysis Generation (Structured Output)                   │
│   ├─ Incident Summary (2-3 sentences)                            │
│   ├─ Root Cause Hypothesis (ranked list)                         │
│   ├─ Severity Assessment (with reasoning)                        │
│   ├─ Suggested Actions (prioritized)                             │
│   └─ Confidence Score (0.0-1.0)                                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ STEP 3: Validation & Storage                                      │
│   ├─ Validate structured output schema                           │
│   ├─ Store analysis in database                                  │
│   ├─ Log telemetry (tokens, latency)                             │
│   └─ Emit domain event                                           │
└──────────────────────────────────────────────────────────────────┘

OUTPUT:
┌──────────────────────────────────────────────────────────────────┐
│ IncidentAnalysis:                                                 │
│   summary: str                                                    │
│   root_cause_hypothesis: list[Hypothesis]                         │
│   severity_assessment: SeverityAssessment                         │
│   suggested_actions: list[Action]                                 │
│   confidence_score: float                                         │
│   context_sources: list[str]                                      │
│   model_metadata: ModelMetadata                                   │
└──────────────────────────────────────────────────────────────────┘
```

### Structured Output Schema (Instructor)

```python
from pydantic import BaseModel, Field

class Hypothesis(BaseModel):
    description: str = Field(..., description="Root cause hypothesis")
    likelihood: float = Field(..., ge=0, le=1, description="Probability 0-1")
    evidence: list[str] = Field(default_factory=list, description="Supporting evidence")

class SeverityAssessment(BaseModel):
    level: str = Field(..., description="critical/high/medium/low")
    reasoning: str = Field(..., description="Why this severity")
    blast_radius: str = Field(..., description="Affected scope")

class Action(BaseModel):
    description: str
    priority: int = Field(..., ge=1, le=5)
    action_type: str = Field(..., description="investigate/mitigate/escalate/communicate")

class IncidentAnalysis(BaseModel):
    summary: str = Field(..., max_length=500)
    root_cause_hypotheses: list[Hypothesis] = Field(..., min_length=1, max_length=5)
    severity_assessment: SeverityAssessment
    suggested_actions: list[Action] = Field(..., min_length=1)
    confidence_score: float = Field(..., ge=0, le=1)
    requires_escalation: bool
    related_services: list[str] = Field(default_factory=list)
```

---

## 15. LangGraph Orchestration Approach

### Design Principles
1. **Start simple**: Single-node graphs first, evolve to multi-agent
2. **State-driven**: Clear state schema for each workflow
3. **Observable**: Every node emits telemetry
4. **Recoverable**: Checkpointing for long-running workflows
5. **Pluggable**: Tools as first-class abstractions

### Initial RCA Graph (Phase 1)

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict

class RCAState(TypedDict):
    incident: dict
    context: list[str]
    analysis: dict | None
    error: str | None

def build_rca_graph():
    graph = StateGraph(RCAState)
    
    # Nodes
    graph.add_node("gather_context", gather_context_node)
    graph.add_node("generate_analysis", generate_analysis_node)
    graph.add_node("validate_output", validate_output_node)
    
    # Edges
    graph.set_entry_point("gather_context")
    graph.add_edge("gather_context", "generate_analysis")
    graph.add_edge("generate_analysis", "validate_output")
    graph.add_edge("validate_output", END)
    
    return graph.compile()
```

### Future Multi-Agent Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AGENT ORCHESTRATOR                            │
│                      (LangGraph)                                 │
└─────────────────────────────────────────────────────────────────┘
         │              │              │              │
         ▼              ▼              ▼              ▼
    ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
    │   RCA   │   │   Log   │   │ Runbook │   │  K8s    │
    │  Agent  │   │ Analyzer│   │  Agent  │   │  Agent  │
    └─────────┘   └─────────┘   └─────────┘   └─────────┘
         │              │              │              │
         └──────────────┴──────────────┴──────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  Shared Tools   │
                    │  & Memory       │
                    └─────────────────┘
```

### Agent Tool Pattern

```python
from langchain_core.tools import tool

@tool
def search_runbooks(query: str, service: str | None = None) -> list[dict]:
    """Search operational runbooks for relevant procedures."""
    # RAG retrieval with metadata filtering
    ...

@tool  
def get_recent_deployments(service: str, hours: int = 24) -> list[dict]:
    """Get recent deployments for a service."""
    # Query deployment API/database
    ...

@tool
def query_metrics(service: str, metric: str, duration: str) -> dict:
    """Query Prometheus metrics for a service."""
    # Prometheus API call
    ...
```

---

## Summary

This architecture provides:

1. **Clean separation**: Domain logic isolated from infrastructure
2. **Async-first**: Non-blocking I/O throughout
3. **Observable**: Traces, metrics, structured logs everywhere
4. **Extensible**: Plugin points for agents, tools, integrations
5. **Production-ready**: Docker, health checks, graceful shutdown
6. **AI-native**: First-class support for LLM operations and RAG

The modular monolith approach allows us to move fast initially while maintaining clear boundaries that enable future decomposition if scale demands it.

Ready to begin implementation with Phase 1: Foundation.
