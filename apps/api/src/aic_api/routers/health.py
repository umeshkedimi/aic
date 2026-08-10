"""Liveness/readiness split (standard k8s probe semantics).

/health: process is up — never checks dependencies, so a slow Postgres
never causes Kubernetes to kill and restart an otherwise-healthy pod.
/ready: dependencies are reachable — used for load-balancer admission,
so traffic doesn't route to a pod that can't yet serve it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aic_api.dependencies import get_db_session, get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> dict[str, str]:
    checks = {"database": "ok", "redis": "ok"}

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "unavailable"

    try:
        await redis.ping()
    except Exception:
        checks["redis"] = "unavailable"

    if any(v != "ok" for v in checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return checks
