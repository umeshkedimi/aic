from __future__ import annotations

from collections.abc import Callable

import httpx
from checkout_service.config import CheckoutServiceSettings
from checkout_service.main import create_app
from fastapi.testclient import TestClient


def _app_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> TestClient:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://payment-service:8000"
    )
    settings = CheckoutServiceSettings(service_version="v-test")
    app = create_app(settings=settings, http_client=client)
    return TestClient(app)


def test_checkout_succeeds_when_payment_succeeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"charge_id": "ch_o1", "status": "succeeded"})

    with _app_with_handler(handler) as client:
        resp = client.post("/checkout", json={"order_id": "o1", "amount_cents": 500})

    assert resp.status_code == 200
    assert resp.json() == {"order_id": "o1", "status": "succeeded"}


def test_checkout_maps_payment_5xx_to_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"charge_id": "", "status": "pool_exhausted"})

    with _app_with_handler(handler) as client:
        resp = client.post("/checkout", json={"order_id": "o2", "amount_cents": 500})

    assert resp.status_code == 502
    assert resp.json()["status"] == "payment_unavailable"


def test_checkout_maps_payment_timeout_to_504() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with _app_with_handler(handler) as client:
        resp = client.post("/checkout", json={"order_id": "o3", "amount_cents": 500})

    assert resp.status_code == 504
    assert resp.json()["status"] == "payment_timeout"


def test_checkout_maps_connection_error_to_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with _app_with_handler(handler) as client:
        resp = client.post("/checkout", json={"order_id": "o4", "amount_cents": 500})

    assert resp.status_code == 502
    assert resp.json()["status"] == "payment_unavailable"


def test_checkout_rejects_non_positive_amount() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not reach payment-service for an invalid request")

    with _app_with_handler(handler) as client:
        resp = client.post("/checkout", json={"order_id": "o5", "amount_cents": 0})

    assert resp.status_code == 422


def test_health_reports_service_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("health check should not call payment-service")

    with _app_with_handler(handler) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "v-test"}


def test_metrics_endpoint_exposes_http_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"charge_id": "ch_o6", "status": "succeeded"})

    with _app_with_handler(handler) as client:
        client.post("/checkout", json={"order_id": "o6", "amount_cents": 500})
        resp = client.get("/metrics")

    assert resp.status_code == 200
    assert "http_requests_total" in resp.text
    assert "http_request_duration_seconds" in resp.text
