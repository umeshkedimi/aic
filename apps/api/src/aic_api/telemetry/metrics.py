"""Prometheus metrics, exposed at /metrics.

Kept to request-level metrics for Phase 1; agent/tool/LLM metrics land
alongside the code that makes those calls (Phase 5+), per
docs/design/21-observability.md.
"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS_TOTAL = Counter(
    "aic_http_requests_total",
    "Total HTTP requests handled",
    labelnames=("method", "path", "status_code"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "aic_http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method", "path"),
)
