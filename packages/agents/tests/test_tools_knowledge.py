"""`knowledge.search` (T12): wired to a real Qdrant, but the tool's own
contract — name/schema/`ToolSpec` behavior — is what this suite tests. The
retrieval quality itself (does a query actually find semantically related
content) is `test_knowledge_store.py`'s job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from aic_agents.knowledge_store import QdrantSettings, index_postmortem
from aic_agents.tools.knowledge import SEARCH, SearchInput, build_specs
from aic_common.clock import FixedClock
from aic_domain.enums import EvidenceStatus
from qdrant_client import AsyncQdrantClient

T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


async def test_search_returns_no_hits_before_anything_is_indexed(qdrant_url: str) -> None:
    client = AsyncQdrantClient(url=qdrant_url)
    settings = QdrantSettings(base_url=qdrant_url, collection=f"pm-tool-test-{uuid4().hex}")
    specs = build_specs(client, settings)

    result = await specs[SEARCH].invoke(FixedClock(T0), SearchInput(query="db pool exhaustion"))

    assert result.status == EvidenceStatus.OK
    assert result.data == []


async def test_search_returns_indexed_hits_as_plain_dicts(qdrant_url: str) -> None:
    client = AsyncQdrantClient(url=qdrant_url)
    settings = QdrantSettings(base_url=qdrant_url, collection=f"pm-tool-test-{uuid4().hex}")
    incident_id = uuid4()
    postmortem_id = uuid4()
    await index_postmortem(
        client=client,
        settings=settings,
        postmortem_id=postmortem_id,
        incident_id=incident_id,
        service="payment-service",
        failure_mode="db_connection_pool_exhaustion",
        resolution_action_type="RollbackDeployment",
        content="the database connection pool was fully exhausted after a bad deploy",
    )
    specs = build_specs(client, settings)

    result = await specs[SEARCH].invoke(
        FixedClock(T0), SearchInput(query="connection pool exhausted")
    )

    assert result.status == EvidenceStatus.OK
    assert isinstance(result.data, list)
    assert len(result.data) >= 1
    hit = result.data[0]
    assert hit["incident_id"] == str(incident_id)
    assert hit["postmortem_id"] == str(postmortem_id)
    assert hit["service"] == "payment-service"
