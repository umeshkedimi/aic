from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from aic_agents.tools.prometheus import (
    INSTANT_QUERY,
    RANGE_QUERY,
    InstantQueryInput,
    PrometheusSettings,
    RangeQueryInput,
    build_specs,
)
from aic_common.clock import FixedClock
from aic_domain.enums import EvidenceStatus

T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="http://prometheus.test")


async def test_range_query_hits_the_real_promql_endpoint_with_correct_params() -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    client = _client(httpx.MockTransport(_handler))
    specs = build_specs(client, PrometheusSettings(base_url="http://prometheus.test"))
    input_data = RangeQueryInput(query='up{service="payment-service"}', start=T0, end=T0)

    result = await specs[RANGE_QUERY].invoke(FixedClock(T0), input_data)

    assert result.status == EvidenceStatus.OK
    assert result.data == {"status": "success", "data": {"result": []}}
    assert captured["path"] == "/api/v1/query_range"
    assert captured["params"]["query"] == 'up{service="payment-service"}'
    await client.aclose()


async def test_instant_query_hits_the_real_instant_endpoint() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    client = _client(httpx.MockTransport(_handler))
    specs = build_specs(client, PrometheusSettings(base_url="http://prometheus.test"))

    result = await specs[INSTANT_QUERY].invoke(FixedClock(T0), InstantQueryInput(query="up"))

    assert result.status == EvidenceStatus.OK
    await client.aclose()


async def test_prometheus_http_error_becomes_an_error_tool_result_not_an_exception() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = _client(httpx.MockTransport(_handler))
    specs = build_specs(client, PrometheusSettings(base_url="http://prometheus.test"))

    result = await specs[RANGE_QUERY].invoke(
        FixedClock(T0), RangeQueryInput(query="up", start=T0, end=T0)
    )

    assert result.status == EvidenceStatus.ERROR
    assert result.error_class == "HTTPStatusError"
    await client.aclose()
