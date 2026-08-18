"""Toy payment-service (design doc §1.2).

`/charge` checks out a connection from a Postgres pool whose size is fixed at
startup by `DB_POOL_SIZE`. Under real load, a pool sized too small for the
traffic genuinely exhausts: requests block on `pool.connection()` up to
`request_timeout_seconds` and then fail with a real 503, not a simulated one.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aic_common.logging import configure_logging, get_logger
from fastapi import FastAPI, Response
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import BaseModel, Field

from payment_service.config import PaymentServiceSettings
from payment_service.db import ConnectionAcquirer, init_schema
from payment_service.metrics import PaymentMetrics

logger = get_logger(__name__)


class ChargeRequest(BaseModel):
    order_id: str
    amount_cents: int = Field(gt=0)


class ChargeResponse(BaseModel):
    charge_id: str
    status: str


def create_app(
    settings: PaymentServiceSettings | None = None,
    pool: ConnectionAcquirer | None = None,
) -> FastAPI:
    """Build the app. Tests pass a fake `pool` to exercise the success/timeout
    paths without a real Postgres; production leaves `pool=None` so the
    lifespan opens (and later closes) a real `AsyncConnectionPool`."""
    settings = settings or PaymentServiceSettings()
    configure_logging(settings.log_level)
    metrics = PaymentMetrics()
    owns_pool = pool is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal pool
        metrics.pool_max_size.set(settings.db_pool_size)
        if owns_pool:
            real_pool = AsyncConnectionPool(
                settings.database_url,
                min_size=settings.db_pool_size,
                max_size=settings.db_pool_size,
                timeout=settings.request_timeout_seconds,
                open=False,
            )
            await real_pool.open(wait=True, timeout=10.0)
            await init_schema(real_pool)
            pool = real_pool
        assert pool is not None
        app.state.pool = pool
        logger.info(
            "payment_service.started",
            db_pool_size=settings.db_pool_size,
            version=settings.service_version,
        )
        try:
            yield
        finally:
            if owns_pool:
                await real_pool.close()

    app = FastAPI(lifespan=lifespan)
    metrics.instrument(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.service_version}

    @app.post("/charge", response_model=ChargeResponse)
    async def charge(req: ChargeRequest, response: Response) -> ChargeResponse:
        active_pool: ConnectionAcquirer = app.state.pool
        start = time.perf_counter()
        try:
            async with active_pool.connection(timeout=settings.request_timeout_seconds) as conn:
                metrics.pool_in_use.inc()
                try:
                    await conn.execute(
                        "INSERT INTO payments (order_id, amount_cents) VALUES (%s, %s)",
                        (req.order_id, req.amount_cents),
                    )
                    await conn.execute("SELECT pg_sleep(%s)", (settings.work_seconds,))
                finally:
                    metrics.pool_in_use.dec()
        except PoolTimeout:
            waited_seconds = time.perf_counter() - start
            logger.warning(
                "payment_service.pool_exhausted",
                order_id=req.order_id,
                waited_seconds=waited_seconds,
                pool_size=settings.db_pool_size,
            )
            response.status_code = 503
            return ChargeResponse(charge_id="", status="pool_exhausted")

        logger.info("payment_service.charge_succeeded", order_id=req.order_id)
        return ChargeResponse(charge_id=f"ch_{req.order_id}", status="succeeded")

    return app


app = create_app()
