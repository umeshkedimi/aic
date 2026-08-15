import asyncio

import httpx
import pytest
from aic_toy_ops.load_generator import run


def test_run_sends_requests_to_checkout_until_duration_elapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"order_id": "x", "status": "succeeded"})

    original_async_client = httpx.AsyncClient

    def fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    asyncio.run(
        run(
            base_url="http://checkout-service:8000",
            concurrency=2,
            interval_seconds=0.01,
            duration_seconds=0.05,
            request_timeout_seconds=1.0,
        )
    )

    assert calls
    assert all(path == "/checkout" for path in calls)
