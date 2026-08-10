import pytest
from httpx import ASGITransport, AsyncClient

from aic_api.app import create_app
from aic_api.settings import ApiSettings


@pytest.fixture
def settings() -> ApiSettings:
    return ApiSettings(
        database_url="postgresql+asyncpg://aic:aic@localhost:5432/aic",
        redis_url="redis://localhost:6379/0",
    )


@pytest.mark.asyncio
async def test_health_does_not_touch_dependencies(settings: ApiSettings) -> None:
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client, app.router.lifespan_context(app):
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_prometheus_format(settings: ApiSettings) -> None:
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client, app.router.lifespan_context(app):
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert b"aic_http_requests_total" in response.content
