"""The toy `payments` table payment-service writes to.

This is the toy system's own database — distinct from AIC's Alembic-managed
system-of-record schema in `packages/database` — so a startup DDL statement
is the right amount of ceremony, not a migration framework.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

_CREATE_PAYMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    order_id TEXT NOT NULL,
    amount_cents BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class ConnectionAcquirer(Protocol):
    """Structural type both the real `AsyncConnectionPool` and test fakes satisfy."""

    def connection(self, timeout: float | None = None) -> AbstractAsyncContextManager[Any]: ...


async def init_schema(pool: ConnectionAcquirer) -> None:
    async with pool.connection() as conn:
        await conn.execute(_CREATE_PAYMENTS_TABLE)
