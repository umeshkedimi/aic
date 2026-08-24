"""`knowledge.search` (design doc §1.9). Stub for T7: the tool exists and
is callable — the `gather` node fans out to it like any other tool, and the
`synthesize` node sees its (empty) result as just another `EvidenceDigest`
— but there's no Qdrant behind it yet, so it always returns no hits. T12
wires this to a real Qdrant search without changing this tool's name or
schema, so nothing downstream needs to change when it does.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aic_agents.tools.base import ToolSpec

SEARCH = "knowledge.search"


class SearchInput(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=50)


async def _search(_input_data: SearchInput) -> Any:
    return []


def build_specs() -> dict[str, ToolSpec[Any]]:
    return {
        SEARCH: ToolSpec(
            name=SEARCH,
            source="knowledge",
            input_model=SearchInput,
            timeout_seconds=5.0,
            rate_limit_key="knowledge",
            rate_limit_max_concurrency=8,
            call=_search,
            render_query=lambda input_data: input_data.query,
        ),
    }
