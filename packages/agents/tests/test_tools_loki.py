from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from aic_agents.tools.loki import QUERY_RANGE, LokiSettings, QueryRangeInput, build_specs
from aic_common.clock import FixedClock
from aic_domain.enums import EvidenceStatus

T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


async def test_query_range_hits_the_real_logql_endpoint_with_nanosecond_bounds() -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://loki.test")
    specs = build_specs(client, LokiSettings(base_url="http://loki.test"))
    input_data = QueryRangeInput(query='{service="payment-service"} |= "error"', start=T0, end=T0)

    result = await specs[QUERY_RANGE].invoke(FixedClock(T0), input_data)

    assert result.status == EvidenceStatus.OK
    assert captured["path"] == "/loki/api/v1/query_range"
    assert captured["params"]["start"] == str(int(T0.timestamp() * 1_000_000_000))
    await client.aclose()


async def test_loki_connection_failure_becomes_an_error_tool_result() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://loki.test")
    specs = build_specs(client, LokiSettings(base_url="http://loki.test"))

    result = await specs[QUERY_RANGE].invoke(
        FixedClock(T0), QueryRangeInput(query="{}", start=T0, end=T0)
    )

    assert result.status == EvidenceStatus.ERROR
    assert result.error_class == "ConnectError"
    await client.aclose()
