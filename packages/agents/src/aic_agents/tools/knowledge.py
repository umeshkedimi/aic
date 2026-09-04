"""`knowledge.search` (design doc §1.9). T7 shipped this as a stub that
always returned no hits; T12 wires it to a real Qdrant search
(`aic_agents.knowledge_store.search`) without changing this tool's name or
input schema, so nothing downstream (the investigation graph's `gather`/
`digest` nodes) needed to change when it did.

**Timeout: 20s, not T7's original 5s.** A real live-cluster run caught
this: `aic_agents.knowledge_store._get_embedder()` lazily loads the real
Sentence Transformers model into memory the *first* time any process calls
`search`/`index_postmortem` — a one-time-per-process cold start that
genuinely took ~5.5s wall time in that run, blowing straight through T7's
original 5.0s budget (chosen when this tool was a stub that returned
instantly). Every call after the first in the same process is fast
(~10ms, confirmed the same run) — the cost is model load, not the
embed-and-query work itself — but a long-lived poller's (aic-investigator)
very first `knowledge.search` call after startup pays this tax for real,
so the timeout needs headroom for it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient

from aic_agents.knowledge_store import QdrantSettings, search
from aic_agents.tools.base import ToolSpec

SEARCH = "knowledge.search"


class SearchInput(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=50)


async def _search(
    client: AsyncQdrantClient, settings: QdrantSettings, input_data: SearchInput
) -> Any:
    hits = await search(
        client=client, settings=settings, query=input_data.query, limit=input_data.limit
    )
    return [hit.model_dump(mode="json") for hit in hits]


def build_specs(client: AsyncQdrantClient, settings: QdrantSettings) -> dict[str, ToolSpec[Any]]:
    return {
        SEARCH: ToolSpec(
            name=SEARCH,
            source="knowledge",
            input_model=SearchInput,
            timeout_seconds=20.0,
            rate_limit_key="knowledge",
            rate_limit_max_concurrency=8,
            call=lambda input_data: _search(client, settings, input_data),
            render_query=lambda input_data: input_data.query,
        ),
    }
