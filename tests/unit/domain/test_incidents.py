"""Unit tests for incident domain models."""

from datetime import datetime, UTC
from uuid import uuid4

import pytest

from aic.domain.incidents import (
    Incident,
    IncidentCreate,
    IncidentSeverity,
    IncidentStatus,
)


class TestIncidentModel:
    """Tests for Incident domain model."""

    def test_incident_is_active_when_open(self):
        """Active incidents should return is_active=True."""
        incident = Incident(
            id=uuid4(),
            title="Test incident",
            severity=IncidentSeverity.HIGH,
            status=IncidentStatus.OPEN,
            source="test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        assert incident.is_active is True

    def test_incident_is_not_active_when_resolved(self):
        """Resolved incidents should return is_active=False."""
        incident = Incident(
            id=uuid4(),
            title="Test incident",
            severity=IncidentSeverity.HIGH,
            status=IncidentStatus.RESOLVED,
            source="test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        assert incident.is_active is False

    def test_incident_is_critical(self):
        """Critical incidents should return is_critical=True."""
        incident = Incident(
            id=uuid4(),
            title="Test incident",
            severity=IncidentSeverity.CRITICAL,
            status=IncidentStatus.OPEN,
            source="test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        assert incident.is_critical is True

    def test_incident_not_critical_for_high_severity(self):
        """High severity incidents should return is_critical=False."""
        incident = Incident(
            id=uuid4(),
            title="Test incident",
            severity=IncidentSeverity.HIGH,
            status=IncidentStatus.OPEN,
            source="test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        assert incident.is_critical is False


class TestIncidentCreate:
    """Tests for IncidentCreate validation."""

    def test_valid_incident_create(self):
        """Valid incident creation data should pass validation."""
        data = IncidentCreate(
            title="Test incident",
            severity=IncidentSeverity.HIGH,
            source="alertmanager",
            service="payment-service",
        )

        assert data.title == "Test incident"
        assert data.severity == IncidentSeverity.HIGH

    def test_incident_create_requires_title(self):
        """Incident creation should require a title."""
        with pytest.raises(ValueError):
            IncidentCreate(
                title="",  # Empty title
                severity=IncidentSeverity.HIGH,
                source="test",
            )

    def test_incident_create_default_tags(self):
        """Tags should default to empty list."""
        data = IncidentCreate(
            title="Test",
            severity=IncidentSeverity.LOW,
            source="test",
        )

        assert data.tags == []
        assert data.metadata == {}
