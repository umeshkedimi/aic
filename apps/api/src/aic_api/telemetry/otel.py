"""Minimal OpenTelemetry wiring: one trace per request.

Exports to an OTLP collector when AIC_OTEL_EXPORTER_OTLP_ENDPOINT is set;
otherwise traces are created and dropped (no collector required for local
dev or tests to pass).
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_tracing(app: FastAPI, *, service_name: str, otlp_endpoint: str | None) -> None:
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    if otlp_endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    # No endpoint configured (local dev, tests): spans are created and
    # dropped rather than printed — a real collector is Phase 4+ setup,
    # and console-dumping every request span is pure noise until then.
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
