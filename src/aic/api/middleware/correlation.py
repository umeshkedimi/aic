"""Request correlation ID middleware.

Ensures every request has a unique correlation ID for tracing
and log correlation across distributed systems.
"""

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from aic.observability.logging import bind_contextvars, clear_contextvars

CORRELATION_ID_HEADER = "X-Correlation-ID"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Middleware that ensures every request has a correlation ID.

    The correlation ID is:
    - Extracted from incoming X-Correlation-ID header if present
    - Generated as a new UUID4 if not present
    - Added to all log messages via structlog contextvars
    - Returned in the response headers
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Get or generate correlation ID
        correlation_id = request.headers.get(CORRELATION_ID_HEADER)
        if not correlation_id:
            correlation_id = str(uuid4())

        # Bind to logging context
        clear_contextvars()
        bind_contextvars(
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
        )

        # Store in request state for access in handlers
        request.state.correlation_id = correlation_id

        # Process request
        response = await call_next(request)

        # Add correlation ID to response
        response.headers[CORRELATION_ID_HEADER] = correlation_id

        return response
