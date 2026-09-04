"""Qdrant-backed knowledge store (design doc §1.13, T12): chunk + embed a
`Postmortem`'s content (Sentence Transformers, per the design doc — a
local model, no external API/key needed, unlike the LLM stages) and index
it into Qdrant tagged with `service`/`failure_mode`/`resolution_action_type`
metadata, so a later incident's `knowledge.search()` can retrieve it.

**Embedding model.** `all-MiniLM-L6-v2` — small (~80MB), fast on CPU, a
well-known sentence-transformers default; 384-dim output fixes the Qdrant
collection's vector size. Loaded once per process (`_get_embedder`,
`functools.lru_cache`) since constructing a `SentenceTransformer` is
expensive (model load, not a network call after the first download) —
never reload it per call. `.encode()` is synchronous/CPU-bound, so every
call here runs it via `asyncio.to_thread`.

**Chunking.** A fixed-size character window with overlap
(`_CHUNK_SIZE`/`_CHUNK_OVERLAP`) — deterministic and dependency-free. The
design doc says "chunked", not how; a postmortem here is a few paragraphs
of scribe-drafted text, not a large document, so a simple window is
proportionate — no sentence-boundary-aware splitter needed for content
this short.

**Collection lifecycle.** `ensure_collection` is idempotent (checks
`collection_exists` before creating) so every caller (the scribe stage
indexing, the `knowledge.search` tool searching) can call it defensively
without risk of a `recreate_collection`-style destructive reset.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from uuid import UUID

from aic_common.config import AICBaseSettings
from aic_common.ids import new_id
from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict
from qdrant_client import AsyncQdrantClient, models
from sentence_transformers import SentenceTransformer

_EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384
_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 100


class QdrantSettings(AICBaseSettings):
    """Qdrant runs as a plain host-reachable Docker container (`make
    qdrant-up`), the same pattern as LiteLLM (T5) — only host processes
    (aic-investigator's `knowledge.search` tool, the new aic-scribe
    poller) ever reach it."""

    model_config = SettingsConfigDict(env_prefix="AIC_QDRANT_")

    base_url: str = "http://localhost:6333"
    collection: str = "postmortems"
    timeout_seconds: float = 10.0


@lru_cache(maxsize=1)
def _get_embedder() -> SentenceTransformer:
    return SentenceTransformer(_EMBEDDING_MODEL_NAME)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    embedder = _get_embedder()
    vectors = await asyncio.to_thread(embedder.encode, texts, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]


async def warm_up_embedder() -> None:
    """Loads the embedding model now, off the event loop, so a long-lived
    poller's (aic-investigator/aic-scribe) *first* real `knowledge.search`/
    `index_postmortem` call doesn't pay the one-time model-load cost — a
    real live-cluster run caught this: cold, it took ~5.5s wall time (see
    `aic_agents.tools.knowledge`'s module docstring for the tool-timeout
    fallout), vs. ~10ms once loaded. Callers should await this once at
    startup, before entering their poll loop."""
    await asyncio.to_thread(_get_embedder)


def chunk_text(
    text: str, *, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP
) -> list[str]:
    """Fixed-size character window with overlap. Never returns an empty
    chunk (a blank/whitespace-only `text` yields `[]`), and always makes
    forward progress (`chunk_size > overlap` is asserted) so this can never
    loop forever on pathological input."""
    assert chunk_size > overlap, "chunk_size must exceed overlap to make forward progress"
    stripped = text.strip()
    if not stripped:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        end = start + chunk_size
        piece = stripped[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end - overlap
    return chunks


class SearchHit(BaseModel):
    incident_id: UUID
    postmortem_id: UUID
    score: float
    chunk_text: str
    service: str
    failure_mode: str
    resolution_action_type: str | None = None


async def ensure_collection(client: AsyncQdrantClient, settings: QdrantSettings) -> None:
    if not await client.collection_exists(settings.collection):
        await client.create_collection(
            collection_name=settings.collection,
            vectors_config=models.VectorParams(
                size=_EMBEDDING_DIM, distance=models.Distance.COSINE
            ),
        )


async def index_postmortem(
    *,
    client: AsyncQdrantClient,
    settings: QdrantSettings,
    postmortem_id: UUID,
    incident_id: UUID,
    service: str,
    failure_mode: str,
    resolution_action_type: str | None,
    content: str,
) -> list[str]:
    """Chunks + embeds `content` and upserts one Qdrant point per chunk.
    Returns the point ids — callers persist these as
    `Postmortem.embedding_refs` (design doc §1.5's `Postmortem` row)."""
    await ensure_collection(client, settings)

    chunks = chunk_text(content)
    if not chunks:
        return []
    vectors = await embed_texts(chunks)

    point_ids = [new_id() for _ in chunks]
    points = [
        models.PointStruct(
            id=str(point_id),
            vector=vector,
            payload={
                "incident_id": str(incident_id),
                "postmortem_id": str(postmortem_id),
                "service": service,
                "failure_mode": failure_mode,
                "resolution_action_type": resolution_action_type,
                "chunk_index": index,
                "chunk_text": chunk,
            },
        )
        for index, (point_id, chunk, vector) in enumerate(
            zip(point_ids, chunks, vectors, strict=True)
        )
    ]
    await client.upsert(collection_name=settings.collection, points=points)
    return [str(point_id) for point_id in point_ids]


async def search(
    *,
    client: AsyncQdrantClient,
    settings: QdrantSettings,
    query: str,
    limit: int,
) -> list[SearchHit]:
    """`knowledge.search`'s real backend (`aic_agents.tools.knowledge`).
    Returns `[]`, not an error, when the collection doesn't exist yet (the
    first run of the signature scenario has nothing to recall — design
    doc §1.13) rather than requiring every caller to special-case a fresh
    deployment."""
    if not await client.collection_exists(settings.collection):
        return []

    [vector] = await embed_texts([query])
    results = await client.query_points(
        collection_name=settings.collection, query=vector, limit=limit
    )
    hits: list[SearchHit] = []
    for point in results.points:
        payload = point.payload or {}
        hits.append(
            SearchHit(
                incident_id=UUID(payload["incident_id"]),
                postmortem_id=UUID(payload["postmortem_id"]),
                score=point.score,
                chunk_text=payload["chunk_text"],
                service=payload["service"],
                failure_mode=payload["failure_mode"],
                resolution_action_type=payload.get("resolution_action_type"),
            )
        )
    return hits
