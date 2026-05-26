"""OpenTelemetry tracing configuration.

Provides distributed tracing across the application with automatic
instrumentation for FastAPI, SQLAlchemy, Redis, and HTTP clients.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.semconv.resource import ResourceAttributes

from aic.config import Settings
from aic.observability.logging import get_logger

logger = get_logger(__name__)

_tracer_provider: TracerProvider | None = None


def setup_tracing(settings: Settings) -> None:
    """Configure OpenTelemetry tracing.

    Sets up tracing with OTLP exporter for production and
    console exporter for development debugging.
    """
    global _tracer_provider

    if not settings.otlp_enabled:
        logger.info("Tracing disabled")
        return

    resource = Resource.create(
        {
            ResourceAttributes.SERVICE_NAME: settings.service_name,
            ResourceAttributes.SERVICE_VERSION: "0.1.0",
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: settings.env,
        }
    )

    _tracer_provider = TracerProvider(resource=resource)

    if settings.is_development and settings.debug:
        # Development: also log to console
        _tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    # OTLP exporter for Jaeger/Tempo
    otlp_exporter = OTLPSpanExporter(
        endpoint=settings.otlp_endpoint,
        insecure=True,
    )
    _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    trace.set_tracer_provider(_tracer_provider)

    # Auto-instrument libraries
    _instrument_libraries()

    logger.info("Tracing configured", endpoint=settings.otlp_endpoint)


def _instrument_libraries() -> None:
    """Instrument common libraries for automatic tracing."""
    try:
        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass

    try:
        RedisInstrumentor().instrument()
    except Exception:
        pass


def instrument_sqlalchemy(engine: object) -> None:
    """Instrument SQLAlchemy engine for tracing.

    Called after engine creation to enable SQL query tracing.
    """
    try:
        SQLAlchemyInstrumentor().instrument(engine=engine)
    except Exception as e:
        logger.warning("Failed to instrument SQLAlchemy", error=str(e))


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer instance for creating spans."""
    return trace.get_tracer(name)


def shutdown_tracing() -> None:
    """Shutdown tracing and flush pending spans."""
    global _tracer_provider
    if _tracer_provider:
        _tracer_provider.shutdown()
        _tracer_provider = None
