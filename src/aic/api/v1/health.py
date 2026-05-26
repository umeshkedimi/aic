"""Health check endpoints.

Provides liveness and readiness probes for Kubernetes and load balancers.
"""

from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from aic.config import Settings, get_settings
from aic.infrastructure.database.session import check_db_health
from aic.infrastructure.cache.redis import check_redis_health

router = APIRouter()


class HealthStatus(str, Enum):
    """Health check status values."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


class ComponentHealth(BaseModel):
    """Health status of a single component."""

    status: HealthStatus
    latency_ms: float | None = None
    message: str | None = None


class HealthResponse(BaseModel):
    """Full health check response."""

    status: HealthStatus
    version: str
    environment: str
    components: dict[str, ComponentHealth]


@router.get("/health", response_model=HealthResponse)
async def health_check(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Comprehensive health check for all dependencies.

    Returns 200 if healthy, 503 if any critical component is unhealthy.
    """
    components: dict[str, ComponentHealth] = {}

    # Check database
    db_health = await check_db_health()
    components["database"] = ComponentHealth(
        status=HealthStatus.HEALTHY if db_health.healthy else HealthStatus.UNHEALTHY,
        latency_ms=db_health.latency_ms,
        message=db_health.message,
    )

    # Check Redis
    redis_health = await check_redis_health()
    components["redis"] = ComponentHealth(
        status=HealthStatus.HEALTHY if redis_health.healthy else HealthStatus.UNHEALTHY,
        latency_ms=redis_health.latency_ms,
        message=redis_health.message,
    )

    # Determine overall status
    unhealthy_count = sum(
        1 for c in components.values() if c.status == HealthStatus.UNHEALTHY
    )

    if unhealthy_count == 0:
        overall_status = HealthStatus.HEALTHY
    elif unhealthy_count == len(components):
        overall_status = HealthStatus.UNHEALTHY
    else:
        overall_status = HealthStatus.DEGRADED

    return HealthResponse(
        status=overall_status,
        version="0.1.0",
        environment=settings.env,
        components=components,
    )


@router.get("/health/live")
async def liveness_probe() -> dict[str, str]:
    """Kubernetes liveness probe.

    Returns 200 if the application process is running.
    Does not check dependencies.
    """
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness_probe() -> dict[str, str]:
    """Kubernetes readiness probe.

    Returns 200 if the application is ready to serve traffic.
    Checks critical dependencies.
    """
    db_health = await check_db_health()
    if not db_health.healthy:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Database not ready")

    return {"status": "ready"}
