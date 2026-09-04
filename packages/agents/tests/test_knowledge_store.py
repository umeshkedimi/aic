"""Qdrant-backed knowledge store (T12). Chunking is pure/deterministic and
tested without any I/O. Indexing/search is tested against a real,
ephemeral Qdrant (`testcontainers`) with the real Sentence Transformers
embedding model — a fake vector store would prove nothing about whether a
query actually retrieves semantically related content, which is the whole
point of this module.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from aic_agents.knowledge_store import (
    QdrantSettings,
    chunk_text,
    index_postmortem,
    search,
    warm_up_embedder,
)
from qdrant_client import AsyncQdrantClient


def test_chunk_text_returns_empty_list_for_blank_text() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_chunk_text_single_chunk_when_shorter_than_chunk_size() -> None:
    text = "a short postmortem body"
    assert chunk_text(text, chunk_size=800, overlap=100) == [text]


def test_chunk_text_splits_long_text_with_overlap() -> None:
    text = "0123456789" * 30  # 300 chars
    chunks = chunk_text(text, chunk_size=100, overlap=20)

    assert len(chunks) > 1
    # Every chunk after the first starts `overlap` chars before the
    # previous chunk's end — reconstructing confirms no gap was skipped.
    assert chunks[0] == text[0:100]
    assert chunks[1] == text[80:180]
    # No pathological infinite loop / empty chunks.
    assert all(chunks)


def test_chunk_text_rejects_overlap_not_smaller_than_chunk_size() -> None:
    with pytest.raises(AssertionError):
        chunk_text("x" * 50, chunk_size=50, overlap=50)


@pytest.fixture
def qdrant_settings(qdrant_url: str) -> QdrantSettings:
    # A fresh, unique collection per test so tests never see each other's
    # points, without needing a teardown/truncate step.
    return QdrantSettings(base_url=qdrant_url, collection=f"pm-test-{uuid4().hex}")


@pytest.fixture
async def qdrant_client(qdrant_url: str) -> AsyncQdrantClient:
    return AsyncQdrantClient(url=qdrant_url)


async def test_warm_up_embedder_makes_the_model_available_for_a_later_embed(
    qdrant_client: AsyncQdrantClient, qdrant_settings: QdrantSettings
) -> None:
    """Real live-cluster run (T12): a fresh process's first `search`/
    `index_postmortem` call pays a real, multi-second embedding-model
    cold-start cost — `warm_up_embedder` exists so a poller can pay it at
    startup instead of on the tool call. Just proves it doesn't raise and
    a subsequent search still works."""
    await warm_up_embedder()
    hits = await search(client=qdrant_client, settings=qdrant_settings, query="anything", limit=5)
    assert hits == []


async def test_search_returns_empty_before_anything_is_indexed(
    qdrant_client: AsyncQdrantClient, qdrant_settings: QdrantSettings
) -> None:
    """Design doc §1.13: "the first run of the signature scenario has
    nothing to recall" — a collection that doesn't exist yet must return
    no hits, not an error."""
    hits = await search(client=qdrant_client, settings=qdrant_settings, query="anything", limit=5)
    assert hits == []


async def test_index_and_search_round_trip_finds_the_relevant_chunk(
    qdrant_client: AsyncQdrantClient, qdrant_settings: QdrantSettings
) -> None:
    incident_id = uuid4()
    postmortem_id = uuid4()
    content = (
        "# Postmortem: payment-service checkout failures\n\n"
        "## Root cause\n"
        "The payment-service deployment v42 reduced DB_POOL_SIZE from 20 to 3, "
        "which caused the database connection pool to become fully exhausted "
        "under normal load, leading to request timeouts and 5xx errors.\n\n"
        "## Action taken\nRolled back payment-service to the prior deployment.\n\n"
        "## Outcome\nLatency and error rate returned to baseline.\n"
    )

    embedding_refs = await index_postmortem(
        client=qdrant_client,
        settings=qdrant_settings,
        postmortem_id=postmortem_id,
        incident_id=incident_id,
        service="payment-service",
        failure_mode="db_connection_pool_exhaustion",
        resolution_action_type="RollbackDeployment",
        content=content,
    )
    assert len(embedding_refs) >= 1

    hits = await search(
        client=qdrant_client,
        settings=qdrant_settings,
        query="database connection pool exhausted after a bad deploy",
        limit=3,
    )

    assert len(hits) >= 1
    top = hits[0]
    assert top.incident_id == incident_id
    assert top.postmortem_id == postmortem_id
    assert top.service == "payment-service"
    assert top.failure_mode == "db_connection_pool_exhaustion"
    assert top.resolution_action_type == "RollbackDeployment"
    assert "pool" in top.chunk_text.lower()
    assert 0.0 < top.score <= 1.0


async def test_search_is_scoped_to_its_own_collection(
    qdrant_client: AsyncQdrantClient, qdrant_settings: QdrantSettings
) -> None:
    """A query against a different (unindexed) collection must not see
    another collection's points, even on the same Qdrant instance."""
    other_settings = QdrantSettings(
        base_url=qdrant_settings.base_url, collection=f"{qdrant_settings.collection}-other"
    )
    await index_postmortem(
        client=qdrant_client,
        settings=qdrant_settings,
        postmortem_id=uuid4(),
        incident_id=uuid4(),
        service="payment-service",
        failure_mode="db_connection_pool_exhaustion",
        resolution_action_type="RollbackDeployment",
        content="pool exhaustion postmortem content",
    )

    hits = await search(
        client=qdrant_client, settings=other_settings, query="pool exhaustion", limit=5
    )
    assert hits == []
