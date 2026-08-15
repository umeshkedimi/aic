"""Toy checkout-service (design doc §1.2).

`/checkout` calls payment-service's `/charge` and maps its real failure
modes (timeout, pool exhaustion, connection refused) onto checkout-service's
own error rate — this is the downstream symptom the signature scenario's
correlator groups with payment-service's own alerts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from aic_common.logging import configure_logging, get_logger
from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from checkout_service.config import CheckoutServiceSettings
from checkout_service.metrics import CheckoutMetrics

logger = get_logger(__name__)


class CheckoutRequest(BaseModel):
    order_id: str
    amount_cents: int = Field(gt=0)


class CheckoutResponse(BaseModel):
    order_id: str
    status: str


def create_app(
    settings: CheckoutServiceSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Build the app. Tests pass a fake `http_client` to exercise
    success/timeout/failure paths without a real payment-service."""
    settings = settings or CheckoutServiceSettings()
    configure_logging(settings.log_level)
    metrics = CheckoutMetrics()
    owns_client = http_client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal http_client
        if owns_client:
            http_client = httpx.AsyncClient(
                base_url=settings.payment_service_url,
                timeout=settings.request_timeout_seconds,
            )
        app.state.http_client = http_client
        logger.info("checkout_service.started", version=settings.service_version)
        try:
            yield
        finally:
            if owns_client:
                assert http_client is not None
                await http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    metrics.instrument(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.service_version}

    @app.post("/checkout", response_model=CheckoutResponse)
    async def checkout(req: CheckoutRequest, response: Response) -> CheckoutResponse:
        client: httpx.AsyncClient = app.state.http_client
        try:
            payment_response = await client.post(
                "/charge",
                json={"order_id": req.order_id, "amount_cents": req.amount_cents},
            )
        except httpx.TimeoutException:
            logger.warning("checkout_service.payment_timeout", order_id=req.order_id)
            response.status_code = 504
            return CheckoutResponse(order_id=req.order_id, status="payment_timeout")
        except httpx.RequestError as exc:
            logger.warning(
                "checkout_service.payment_unreachable", order_id=req.order_id, error=str(exc)
            )
            response.status_code = 502
            return CheckoutResponse(order_id=req.order_id, status="payment_unavailable")

        if payment_response.status_code != 200:
            logger.warning(
                "checkout_service.payment_failed",
                order_id=req.order_id,
                payment_status_code=payment_response.status_code,
            )
            response.status_code = 502
            return CheckoutResponse(order_id=req.order_id, status="payment_unavailable")

        logger.info("checkout_service.checkout_succeeded", order_id=req.order_id)
        return CheckoutResponse(order_id=req.order_id, status="succeeded")

    return app


app = create_app()
