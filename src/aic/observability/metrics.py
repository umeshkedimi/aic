"""Prometheus metrics configuration.

Provides application metrics for monitoring including request latencies,
error rates, and AI-specific metrics like token usage.
"""

from prometheus_client import Counter, Histogram, Gauge, Info

from aic.config import Settings
from aic.observability.logging import get_logger

logger = get_logger(__name__)

# =============================================================================
# HTTP Metrics
# =============================================================================

HTTP_REQUESTS_TOTAL = Counter(
    "aic_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "aic_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# =============================================================================
# Incident Metrics
# =============================================================================

INCIDENTS_TOTAL = Counter(
    "aic_incidents_total",
    "Total incidents ingested",
    ["severity", "source"],
)

INCIDENTS_ACTIVE = Gauge(
    "aic_incidents_active",
    "Currently active incidents",
    ["severity"],
)

INCIDENT_ANALYSIS_DURATION_SECONDS = Histogram(
    "aic_incident_analysis_duration_seconds",
    "Time to analyze an incident",
    ["analysis_type"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# =============================================================================
# LLM Metrics
# =============================================================================

LLM_REQUESTS_TOTAL = Counter(
    "aic_llm_requests_total",
    "Total LLM API requests",
    ["model", "operation", "status"],
)

LLM_REQUEST_DURATION_SECONDS = Histogram(
    "aic_llm_request_duration_seconds",
    "LLM request duration in seconds",
    ["model", "operation"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

LLM_TOKENS_TOTAL = Counter(
    "aic_llm_tokens_total",
    "Total LLM tokens used",
    ["model", "token_type"],  # token_type: input, output
)

# =============================================================================
# RAG Metrics
# =============================================================================

RAG_RETRIEVAL_DURATION_SECONDS = Histogram(
    "aic_rag_retrieval_duration_seconds",
    "RAG retrieval duration in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

RAG_DOCUMENTS_RETRIEVED = Histogram(
    "aic_rag_documents_retrieved",
    "Number of documents retrieved per query",
    buckets=(1, 2, 3, 5, 10, 20),
)

# =============================================================================
# Database Metrics
# =============================================================================

DB_QUERY_DURATION_SECONDS = Histogram(
    "aic_db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

DB_POOL_SIZE = Gauge(
    "aic_db_pool_size",
    "Database connection pool size",
)

# =============================================================================
# Application Info
# =============================================================================

APP_INFO = Info(
    "aic_app",
    "Application information",
)


def setup_metrics(settings: Settings) -> None:
    """Configure application metrics."""
    if not settings.metrics_enabled:
        logger.info("Metrics disabled")
        return

    APP_INFO.info(
        {
            "version": "0.1.0",
            "environment": settings.env,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.openai_model,
        }
    )

    logger.info("Metrics configured")
