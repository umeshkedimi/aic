"""Request timing middleware.

Tracks request duration and records metrics for monitoring.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from aic.observability.logging import get_logger
from aic.observability.metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION_SECONDS

logger = get_logger(__name__)


class TimingMiddleware(BaseHTTPMiddleware):
    """Middleware that tracks request timing and records metrics."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start_time = time.perf_counter()

        response = await call_next(request)

        duration = time.perf_counter() - start_time

        # Normalize endpoint path for metrics (avoid high cardinality)
        endpoint = self._normalize_path(request.url.path)

        # Record metrics
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            endpoint=endpoint,
            status_code=response.status_code,
        ).inc()

        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=request.method,
            endpoint=endpoint,
        ).observe(duration)

        # Add timing header
        response.headers["X-Response-Time"] = f"{duration:.3f}s"

        # Log slow requests
        if duration > 1.0:
            logger.warning(
                "Slow request",
                duration_seconds=round(duration, 3),
                status_code=response.status_code,
            )

        return response

    def _normalize_path(self, path: str) -> str:
        """Normalize path to reduce metric cardinality.

        Replaces dynamic path segments (UUIDs, IDs) with placeholders.
        """
        parts = path.strip("/").split("/")
        normalized_parts = []

        for part in parts:
            # Replace UUIDs and numeric IDs with placeholders
            if self._is_uuid(part):
                normalized_parts.append("{id}")
            elif part.isdigit():
                normalized_parts.append("{id}")
            else:
                normalized_parts.append(part)

        return "/" + "/".join(normalized_parts) if normalized_parts else "/"

    def _is_uuid(self, value: str) -> bool:
        """Check if string is a UUID."""
        if len(value) == 36 and value.count("-") == 4:
            try:
                parts = value.split("-")
                return len(parts) == 5 and all(
                    len(p) == n for p, n in zip(parts, [8, 4, 4, 4, 12])
                )
            except (ValueError, AttributeError):
                return False
        return False
