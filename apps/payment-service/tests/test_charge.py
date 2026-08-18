from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from payment_service.config import PaymentServiceSettings
from payment_service.main import create_app
from psycopg_pool import PoolTimeout


class FakePool:
    """Structurally satisfies `ConnectionAcquirer` without touching Postgres."""

    def __init__(self, *, should_timeout: bool = False) -> None:
        self.should_timeout = should_timeout
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def connection(self, timeout: float | None = None) -> Any:
        should_timeout = self.should_timeout
        executed = self.executed

        @asynccontextmanager
        async def _acquire() -> Any:
            if should_timeout:
                raise PoolTimeout("simulated exhaustion")

            async def execute(query: str, params: tuple[Any, ...] = ()) -> None:
                executed.append((query, params))

            yield SimpleNamespace(execute=execute)

        return _acquire()


def _settings() -> PaymentServiceSettings:
    return PaymentServiceSettings(database_url="unused", service_version="v-test")


def test_charge_succeeds_and_records_work() -> None:
    pool = FakePool()
    app = create_app(settings=_settings(), pool=pool)
    with TestClient(app) as client:
        resp = client.post("/charge", json={"order_id": "o1", "amount_cents": 500})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["charge_id"] == "ch_o1"
    assert any("INSERT INTO payments" in q for q, _ in pool.executed)


def test_charge_returns_503_when_pool_exhausted() -> None:
    app = create_app(settings=_settings(), pool=FakePool(should_timeout=True))
    with TestClient(app) as client:
        resp = client.post("/charge", json={"order_id": "o2", "amount_cents": 500})

    assert resp.status_code == 503
    assert resp.json()["status"] == "pool_exhausted"


def test_charge_rejects_non_positive_amount() -> None:
    app = create_app(settings=_settings(), pool=FakePool())
    with TestClient(app) as client:
        resp = client.post("/charge", json={"order_id": "o3", "amount_cents": 0})

    assert resp.status_code == 422


def test_health_reports_service_version() -> None:
    app = create_app(settings=_settings(), pool=FakePool())
    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "v-test"}


def test_metrics_endpoint_exposes_pool_and_http_metrics() -> None:
    app = create_app(settings=_settings(), pool=FakePool())
    with TestClient(app) as client:
        client.post("/charge", json={"order_id": "o4", "amount_cents": 500})
        resp = client.get("/metrics")

    assert resp.status_code == 200
    assert "db_pool_connections_in_use" in resp.text
    assert "db_pool_max_size" in resp.text
    assert "http_requests_total" in resp.text
    assert "http_request_duration_seconds" in resp.text
