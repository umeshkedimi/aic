"""API v1 router aggregating all endpoint modules."""

from fastapi import APIRouter

from aic.api.v1.health import router as health_router
from aic.api.v1.incidents import router as incidents_router

api_v1_router = APIRouter()

# Health checks (no prefix, no auth)
api_v1_router.include_router(health_router, tags=["Health"])

# Incidents API
api_v1_router.include_router(incidents_router, prefix="/incidents", tags=["Incidents"])
