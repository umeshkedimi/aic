"""`EventBusPort`/`EventConsumerPort` (ADR 0002 consequence): the seam a
future swap to a managed event bus would only need to touch here, not at
every producer/consumer call site in `aic-ingest`/`aic-correlator`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel


class ConsumedMessage(BaseModel):
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes


class EventBusPort(Protocol):
    async def publish(self, topic: str, key: str, value: bytes) -> None: ...


class EventConsumerPort(Protocol):
    def __aiter__(self) -> AsyncIterator[ConsumedMessage]: ...

    async def commit(self) -> None: ...
