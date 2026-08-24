"""`loki.query_range` (design doc §1.9): real LogQL reads against Loki's
HTTP API, filtered to `level=error` by the caller's query string per §1.4's
INVESTIGATE-gather row.

Loki has no in-cluster-only restriction here on purpose — like Prometheus
(T3) it gets a kind NodePort/hostPort so this host process can reach it
without a standing `kubectl port-forward`; see `infra/kind/kind-config.yaml`
and `infra/kind/observability/loki.yaml`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from aic_common.config import AICBaseSettings
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict

from aic_agents.tools.base import ToolSpec

QUERY_RANGE = "loki.query_range"


class LokiSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_LOKI_")

    base_url: str = "http://localhost:3100"
    timeout_seconds: float = 10.0


class QueryRangeInput(BaseModel):
    query: str
    start: datetime
    end: datetime
    limit: int = Field(default=200, ge=1, le=5000)


async def _query_range(client: httpx.AsyncClient, input_data: QueryRangeInput) -> Any:
    response = await client.get(
        "/loki/api/v1/query_range",
        params={
            "query": input_data.query,
            # Loki's HTTP API accepts unix-epoch nanoseconds for start/end.
            "start": int(input_data.start.timestamp() * 1_000_000_000),
            "end": int(input_data.end.timestamp() * 1_000_000_000),
            "limit": input_data.limit,
        },
    )
    response.raise_for_status()
    return response.json()


def build_specs(client: httpx.AsyncClient, settings: LokiSettings) -> dict[str, ToolSpec[Any]]:
    return {
        QUERY_RANGE: ToolSpec(
            name=QUERY_RANGE,
            source="loki",
            input_model=QueryRangeInput,
            timeout_seconds=settings.timeout_seconds,
            rate_limit_key="loki",
            rate_limit_max_concurrency=4,
            call=lambda input_data: _query_range(client, input_data),
            render_query=lambda input_data: input_data.query,
        ),
    }
