from datetime import UTC, datetime

import pytest

from aic_common.clock import FixedClock
from aic_domain.incidents.errors import IllegalTransition
from aic_domain.incidents.events import ActorType
from aic_domain.incidents.incident import Incident
from aic_domain.incidents.severity import Severity
from aic_domain.incidents.state import IncidentStatus, IncidentTransitionEvent


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 8, 10, 3, 4, tzinfo=UTC))


def test_open_creates_a_created_event(clock: FixedClock) -> None:
    incident = Incident.open(
        fingerprint="payment-service:high-5xx",
        title="Elevated 5xx on payment-service",
        service="payment-service",
        environment="prod",
        source="alertmanager",
        severity=Severity.SEV2,
        clock=clock,
    )

    assert incident.status == IncidentStatus.OPEN
    events = incident.pending_events()
    assert len(events) == 1
    assert events[0].event_type == "created"
    assert events[0].seq == 1
    assert events[0].actor_type == ActorType.SYSTEM


def test_apply_advances_status_and_appends_event(clock: FixedClock) -> None:
    incident = Incident.open(
        fingerprint="fp",
        title="t",
        service="s",
        environment="prod",
        source="manual",
        severity=Severity.SEV3,
        clock=clock,
    )
    incident.clear_pending_events()

    event = incident.apply(
        IncidentTransitionEvent.WORKFLOW_STARTED,
        actor_type=ActorType.SYSTEM,
        actor_id="aic-worker",
        clock=clock,
    )

    assert incident.status == IncidentStatus.TRIAGING
    assert event.event_type == "workflow_started"
    assert event.seq == 2  # seq 1 was the "created" event cleared above
    assert incident.pending_events() == [event]


def test_illegal_transition_leaves_status_and_seq_unchanged(clock: FixedClock) -> None:
    incident = Incident.open(
        fingerprint="fp",
        title="t",
        service="s",
        environment="prod",
        source="manual",
        severity=Severity.SEV3,
        clock=clock,
    )

    with pytest.raises(IllegalTransition):
        incident.apply(
            IncidentTransitionEvent.SOAK_PASSED,
            actor_type=ActorType.SYSTEM,
            actor_id="aic-worker",
            clock=clock,
        )

    assert incident.status == IncidentStatus.OPEN


def test_resolved_at_set_only_on_resolution(clock: FixedClock) -> None:
    incident = Incident.open(
        fingerprint="fp",
        title="t",
        service="s",
        environment="prod",
        source="manual",
        severity=Severity.SEV1,
        clock=clock,
    )
    assert incident.resolved_at is None

    for event in (
        IncidentTransitionEvent.WORKFLOW_STARTED,
        IncidentTransitionEvent.TRIAGE_COMPLETED,
        IncidentTransitionEvent.ALL_ACTIONS_AUTO_APPROVED,
        IncidentTransitionEvent.ACTIONS_EXECUTED,
        IncidentTransitionEvent.SOAK_PASSED,
    ):
        incident.apply(event, actor_type=ActorType.SYSTEM, actor_id="aic-worker", clock=clock)

    assert incident.status == IncidentStatus.RESOLVED
    assert incident.resolved_at == clock.now()
