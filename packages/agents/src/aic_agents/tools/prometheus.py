"""`prometheus.range_query` / `prometheus.instant_query` (design doc §1.9):
real PromQL reads against Prometheus's HTTP API. Comparing the incident
window against the 1h-prior baseline (§1.4) is the `plan`/`gather` nodes'
job — this module only knows how to run one PromQL query and return the
raw JSON response.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from aic_common.config import AICBaseSettings
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict

from aic_agents.tools.base import ToolSpec

RANGE_QUERY = "prometheus.range_query"
INSTANT_QUERY = "prometheus.instant_query"


class PrometheusSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_PROMETHEUS_")

    base_url: str = "http://localhost:9090"
    timeout_seconds: float = 10.0


class RangeQueryInput(BaseModel):
    query: str
    start: datetime
    end: datetime
    step_seconds: int = Field(default=15, ge=1)


class InstantQueryInput(BaseModel):
    query: str
    time: datetime | None = None


async def _range_query(client: httpx.AsyncClient, input_data: RangeQueryInput) -> Any:
    response = await client.get(
        "/api/v1/query_range",
        params={
            "query": input_data.query,
            "start": input_data.start.timestamp(),
            "end": input_data.end.timestamp(),
            "step": input_data.step_seconds,
        },
    )
    response.raise_for_status()
    return response.json()


async def _instant_query(client: httpx.AsyncClient, input_data: InstantQueryInput) -> Any:
    params: dict[str, Any] = {"query": input_data.query}
    if input_data.time is not None:
        params["time"] = input_data.time.timestamp()
    response = await client.get("/api/v1/query", params=params)
    response.raise_for_status()
    return response.json()


def build_specs(
    client: httpx.AsyncClient, settings: PrometheusSettings
) -> dict[str, ToolSpec[Any]]:
    return {
        RANGE_QUERY: ToolSpec(
            name=RANGE_QUERY,
            source="prometheus",
            input_model=RangeQueryInput,
            timeout_seconds=settings.timeout_seconds,
            rate_limit_key="prometheus",
            rate_limit_max_concurrency=4,
            call=lambda input_data: _range_query(client, input_data),
            render_query=lambda input_data: input_data.query,
        ),
        INSTANT_QUERY: ToolSpec(
            name=INSTANT_QUERY,
            source="prometheus",
            input_model=InstantQueryInput,
            timeout_seconds=settings.timeout_seconds,
            rate_limit_key="prometheus",
            rate_limit_max_concurrency=4,
            call=lambda input_data: _instant_query(client, input_data),
            render_query=lambda input_data: input_data.query,
        ),
    }
