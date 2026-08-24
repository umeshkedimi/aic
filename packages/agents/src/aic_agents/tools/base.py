"""Common tool-adapter machinery (design doc §1.9).

Every investigation tool declares its own input schema, an explicit
timeout (no unbounded awaits), a rate-limit key that protects the *target*
system (Prometheus/Loki/K8s must not be hammered by a runaway assess/gather
loop), and how its raw output gets compressed into `Evidence.result_digest`.

Tool failures surface to the graph as data, never exceptions: a dead Loki
instance becomes `ToolResult(status=ERROR, error_class=...)`, which the
`gather` node turns into a real `Evidence` row ("we couldn't see logs for
X") — honest investigative signal, not a crash.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aic_common.clock import Clock
from aic_domain.enums import EvidenceStatus
from pydantic import BaseModel

_MAX_DIGEST_CHARS = 4000


def default_digest(data: Any) -> str:
    """Bound raw tool output to a size sane for a Postgres text column.

    This is a storage cap for `Evidence.result_digest`, not the LLM
    `digest` node's summarization (`aic_agents.graphs.investigation`) —
    that node does the actual compression-with-judgment; this just stops a
    single tool call from writing megabytes into one row.
    """
    rendered = json.dumps(data, default=str)
    if len(rendered) > _MAX_DIGEST_CHARS:
        return rendered[:_MAX_DIGEST_CHARS] + "...(truncated)"
    return rendered


@dataclass(slots=True)
class ToolResult:
    tool: str
    source: str
    status: EvidenceStatus
    query: str | None = None
    data: Any = None
    error_class: str | None = None
    error_message: str | None = None
    latency_ms: int = 0
    collected_at: datetime | None = None


class _KeyedRateLimiter:
    """Bounds concurrent in-flight calls per rate-limit key so a runaway
    assess/gather loop can't hammer a shared target. Process-wide and keyed
    by string so every `ToolSpec` naming the same key shares one limit —
    intentionally a plain semaphore, not a precise token bucket: the design
    doc's requirement is "protects the target", not a specific rate curve.
    """

    def __init__(self) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def semaphore(self, key: str, *, max_concurrency: int) -> asyncio.Semaphore:
        if key not in self._semaphores:
            self._semaphores[key] = asyncio.Semaphore(max_concurrency)
        return self._semaphores[key]


_RATE_LIMITER = _KeyedRateLimiter()


@dataclass(slots=True)
class ToolSpec[TIn: BaseModel]:
    name: str
    source: str
    input_model: type[TIn]
    timeout_seconds: float
    rate_limit_key: str
    rate_limit_max_concurrency: int
    call: Callable[[TIn], Awaitable[Any]]
    render_query: Callable[[TIn], str | None]
    digest: Callable[[Any], str] = default_digest

    async def invoke(self, clock: Clock, input_data: TIn) -> ToolResult:
        semaphore = _RATE_LIMITER.semaphore(
            self.rate_limit_key, max_concurrency=self.rate_limit_max_concurrency
        )
        query = self.render_query(input_data)
        started = time.monotonic()
        async with semaphore:
            try:
                data = await asyncio.wait_for(self.call(input_data), timeout=self.timeout_seconds)
            except Exception as exc:
                return ToolResult(
                    tool=self.name,
                    source=self.source,
                    status=EvidenceStatus.ERROR,
                    query=query,
                    error_class=type(exc).__name__,
                    error_message=str(exc),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    collected_at=clock.now(),
                )
        return ToolResult(
            tool=self.name,
            source=self.source,
            status=EvidenceStatus.OK,
            query=query,
            data=data,
            latency_ms=int((time.monotonic() - started) * 1000),
            collected_at=clock.now(),
        )
