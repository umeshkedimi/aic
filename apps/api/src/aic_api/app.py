from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from aic_api.middleware.correlation import CorrelationIdMiddleware
from aic_api.middleware.metrics import MetricsMiddleware
from aic_api.routers import health, metrics
from aic_api.settings import ApiSettings
from aic_api.telemetry.otel import configure_tracing
from aic_common.logging import configure_logging
from aic_database.session import create_engine, create_session_factory


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    configure_logging(service_name=settings.service_name, level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = Redis.from_url(settings.redis_url)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await engine.dispose()

    app = FastAPI(title="AIC API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)

    configure_tracing(
        app,
        service_name=settings.service_name,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    )

    return app
