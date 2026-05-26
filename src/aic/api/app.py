"""FastAPI application factory.

Creates and configures the FastAPI application with all middleware,
routers, and lifecycle management.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from aic.config import Settings, get_settings
from aic.api.middleware.correlation import CorrelationMiddleware
from aic.api.middleware.error_handler import setup_exception_handlers
from aic.api.middleware.timing import TimingMiddleware
from aic.api.v1.router import api_v1_router
from aic.observability import setup_logging, setup_tracing, setup_metrics, get_logger
from aic.infrastructure.database.session import init_db, close_db
from aic.infrastructure.cache.redis import init_redis, close_redis

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup/shutdown."""
    settings: Settings = app.state.settings
    logger.info(
        "Starting AIC",
        env=settings.env,
        debug=settings.debug,
    )

    # Initialize infrastructure
    await init_db(settings)
    await init_redis(settings)

    logger.info("AIC started successfully")

    yield

    # Shutdown
    logger.info("Shutting down AIC")
    await close_redis()
    await close_db()
    logger.info("AIC shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Factory function to create FastAPI application.

    Args:
        settings: Optional settings override. If not provided,
                  settings are loaded from environment.

    Returns:
        Configured FastAPI application instance.
    """
    if settings is None:
        settings = get_settings()

    # Setup observability first
    setup_logging(settings)
    setup_tracing(settings)
    setup_metrics(settings)

    app = FastAPI(
        title="AIC - AI Incident Commander",
        description="AI-powered operational intelligence platform for incident management",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    app.state.settings = settings

    # Add middleware (order matters - first added is outermost)
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
    setup_exception_handlers(app)

    # API routes
    app.include_router(api_v1_router, prefix="/api/v1")

    # Metrics endpoint (separate from API)
    if settings.metrics_enabled:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

    return app
