# AIC - AI Incident Commander

AI-powered operational intelligence platform for incident management, root cause analysis, and intelligent remediation.

## Overview

AIC is an enterprise-grade platform that:

- Ingests operational alerts/events/incidents from multiple sources
- Performs AI-driven root cause analysis (RCA)
- Retrieves relevant operational knowledge using RAG
- Orchestrates AI agents for investigation
- Recommends or executes remediation workflows
- Maintains full observability and audit trails

## Quick Start

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- OpenAI API key (or Azure OpenAI)

### Setup

1. **Clone and setup environment:**

```bash
cd aic
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

2. **Start the infrastructure:**

```bash
make docker-up
```

3. **Run database migrations:**

```bash
make migrate
```

4. **Access the services:**

- API: http://localhost:8000
- API Docs: http://localhost:8000/docs
- Grafana: http://localhost:3000 (admin/admin)
- Jaeger UI: http://localhost:16686
- Prometheus: http://localhost:9090

### Development

```bash
# Install dependencies
make install

# Run development server (with hot reload)
make dev

# Run tests
make test

# Run linting and type checking
make check
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         API Layer                                │
│                        (FastAPI)                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Application    │  │    Domain       │  │ Infrastructure  │
│    Services     │  │    Models       │  │   (DB, Cache)   │
└─────────────────┘  └─────────────────┘  └─────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AI Intelligence Layer                         │
│            (RCA Engine, RAG Pipeline, Agents)                   │
└─────────────────────────────────────────────────────────────────┘
```

## API Endpoints

### Incidents

- `POST /api/v1/incidents` - Create incident
- `GET /api/v1/incidents` - List incidents
- `GET /api/v1/incidents/{id}` - Get incident
- `PATCH /api/v1/incidents/{id}` - Update incident
- `DELETE /api/v1/incidents/{id}` - Delete incident
- `GET /api/v1/incidents/stats` - Get statistics

### Health

- `GET /api/v1/health` - Full health check
- `GET /api/v1/health/live` - Liveness probe
- `GET /api/v1/health/ready` - Readiness probe

## Tech Stack

- **Backend**: FastAPI, AsyncIO, Pydantic v2
- **Database**: PostgreSQL with SQLAlchemy 2.0 async
- **Cache**: Redis
- **Vector Store**: Qdrant
- **AI/LLM**: OpenAI, LangGraph, Instructor
- **Observability**: OpenTelemetry, Prometheus, Grafana, Jaeger

## Project Structure

```
aic/
├── src/aic/
│   ├── api/           # FastAPI routes and middleware
│   ├── application/   # Application services (use cases)
│   ├── domain/        # Business logic and models
│   ├── infrastructure/# Database, cache, external services
│   ├── ai/            # AI/ML components
│   └── observability/ # Logging, tracing, metrics
├── migrations/        # Alembic database migrations
├── tests/             # Test suite
├── docker/            # Docker configuration
└── docs/              # Documentation
```

## Configuration

All configuration is via environment variables with `AIC_` prefix:

| Variable | Description | Default |
|----------|-------------|---------|
| `AIC_ENV` | Environment (development/staging/production) | development |
| `AIC_DEBUG` | Enable debug mode | false |
| `AIC_DATABASE_URL` | PostgreSQL connection URL | - |
| `AIC_REDIS_URL` | Redis connection URL | - |
| `OPENAI_API_KEY` | OpenAI API key | - |

See `.env.example` for full list.

## License

MIT
