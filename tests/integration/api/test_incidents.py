"""Integration tests for incident API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestIncidentAPI:
    """Integration tests for /api/v1/incidents endpoints."""

    async def test_create_incident(self, client: AsyncClient, sample_incident_data: dict):
        """Should create a new incident."""
        response = await client.post("/api/v1/incidents", json=sample_incident_data)

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == sample_incident_data["title"]
        assert data["severity"] == sample_incident_data["severity"]
        assert data["status"] == "open"
        assert "id" in data

    async def test_create_incident_with_external_id(
        self, client: AsyncClient, sample_incident_with_external_id: dict
    ):
        """Should create incident with external ID."""
        response = await client.post(
            "/api/v1/incidents", json=sample_incident_with_external_id
        )

        assert response.status_code == 201
        data = response.json()
        assert data["external_id"] == sample_incident_with_external_id["external_id"]

    async def test_get_incident(self, client: AsyncClient, sample_incident_data: dict):
        """Should retrieve incident by ID."""
        # Create first
        create_response = await client.post(
            "/api/v1/incidents", json=sample_incident_data
        )
        incident_id = create_response.json()["id"]

        # Then get
        response = await client.get(f"/api/v1/incidents/{incident_id}")

        assert response.status_code == 200
        assert response.json()["id"] == incident_id

    async def test_get_nonexistent_incident(self, client: AsyncClient):
        """Should return 404 for nonexistent incident."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = await client.get(f"/api/v1/incidents/{fake_id}")

        assert response.status_code == 404

    async def test_list_incidents(self, client: AsyncClient, sample_incident_data: dict):
        """Should list incidents."""
        # Create an incident first
        await client.post("/api/v1/incidents", json=sample_incident_data)

        response = await client.get("/api/v1/incidents")

        assert response.status_code == 200
        data = response.json()
        assert "incidents" in data
        assert isinstance(data["incidents"], list)

    async def test_list_incidents_filter_by_severity(
        self, client: AsyncClient, sample_incident_data: dict
    ):
        """Should filter incidents by severity."""
        await client.post("/api/v1/incidents", json=sample_incident_data)

        response = await client.get("/api/v1/incidents?severity=high")

        assert response.status_code == 200

    async def test_update_incident_status(
        self, client: AsyncClient, sample_incident_data: dict
    ):
        """Should update incident status."""
        # Create first
        create_response = await client.post(
            "/api/v1/incidents", json=sample_incident_data
        )
        incident_id = create_response.json()["id"]

        # Update status
        response = await client.patch(
            f"/api/v1/incidents/{incident_id}",
            json={"status": "investigating"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "investigating"

    async def test_delete_incident(
        self, client: AsyncClient, sample_incident_data: dict
    ):
        """Should delete incident."""
        # Create first
        create_response = await client.post(
            "/api/v1/incidents", json=sample_incident_data
        )
        incident_id = create_response.json()["id"]

        # Delete
        response = await client.delete(f"/api/v1/incidents/{incident_id}")
        assert response.status_code == 204

        # Verify deleted
        get_response = await client.get(f"/api/v1/incidents/{incident_id}")
        assert get_response.status_code == 404


@pytest.mark.asyncio
class TestHealthAPI:
    """Integration tests for health endpoints."""

    async def test_liveness_probe(self, client: AsyncClient):
        """Should return alive status."""
        response = await client.get("/api/v1/health/live")

        assert response.status_code == 200
        assert response.json()["status"] == "alive"
