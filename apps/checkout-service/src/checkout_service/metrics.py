"""Prometheus instrumentation.

Deliberately duplicated (not shared) with payment-service's identical
module: two ~50-line files are simpler and more testable in isolation than
a cross-app dependency, and each app owns its own `CollectorRegistry` so
importing both in one test process never collides on the default registry.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)


class CheckoutMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.request_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "path", "status"],
            registry=self.registry,
        )
        self.requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "path", "status"],
            registry=self.registry,
        )

    def instrument(self, app: FastAPI) -> None:
        @app.middleware("http")
        async def _record_metrics(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            start = time.perf_counter()
            response = await call_next(request)
            duration = time.perf_counter() - start
            path = request.url.path
            status = str(response.status_code)
            self.request_duration.labels(request.method, path, status).observe(duration)
            self.requests_total.labels(request.method, path, status).inc()
            return response

        @app.get("/metrics")
        async def metrics() -> Response:
            return Response(generate_latest(self.registry), media_type=CONTENT_TYPE_LATEST)
