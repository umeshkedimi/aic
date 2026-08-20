from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_contracts.events import AlertEvent
from aic_correlator.correlate import process_alert_event
from aic_database.models import Incident, IncidentEvent, IncidentSignal
from aic_domain.correlation import DEFAULT_SERVICE_DEPENDENCIES, ServiceDependencyGraph
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

GRAPH = ServiceDependencyGraph.from_pairs(DEFAULT_SERVICE_DEPENDENCIES)
T0 = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def _event(**overrides: object) -> AlertEvent:
    defaults: dict[str, object] = dict(
        alert_fingerprint=f"fp-{uuid.uuid4().hex[:8]}",
        alertname="HighLatencyPaymentService",
        service="payment-service",
        environment=Environment.LOCAL,
        severity_label="critical",
        labels={"alertname": "HighLatencyPaymentService", "service": "payment-service"},
        starts_at=T0,
        received_at=T0,
    )
    defaults.update(overrides)
    return AlertEvent.model_validate(defaults)


def test_first_alert_opens_an_incident_with_one_signal_and_audit_events(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id = process_alert_event(session, _event(), graph=GRAPH, clock=FixedClock(T0))
        session.commit()

        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status.value == "triaging"  # workflow_started fired

        signals = (
            session.execute(select(IncidentSignal).where(IncidentSignal.incident_id == incident_id))
            .scalars()
            .all()
        )
        assert len(signals) == 1

        events = (
            session.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.seq)
            )
            .scalars()
            .all()
        )
        assert [e.event_type for e in events] == ["alert_signal_attached", "workflow_started"]
        assert [e.seq for e in events] == [1, 2]


def test_second_correlated_alert_attaches_to_the_same_incident(
    session_factory: sessionmaker[Session],
) -> None:
    fingerprint_group = f"grp-{uuid.uuid4().hex[:8]}"
    service = f"payment-{fingerprint_group}"
    graph = ServiceDependencyGraph.from_pairs([(f"checkout-{fingerprint_group}", service)])

    with session_factory() as session:
        first_id = process_alert_event(
            session, _event(service=service), graph=graph, clock=FixedClock(T0)
        )
        session.commit()

        second_id = process_alert_event(
            session,
            _event(
                service=service,
                alertname="HighErrorRatePaymentService",
                starts_at=T0 + timedelta(minutes=1),
            ),
            graph=graph,
            clock=FixedClock(T0 + timedelta(minutes=1)),
        )
        session.commit()

        assert second_id == first_id
        signals = (
            session.execute(select(IncidentSignal).where(IncidentSignal.incident_id == first_id))
            .scalars()
            .all()
        )
        assert len(signals) == 2


def test_replaying_the_same_alert_fingerprint_does_not_duplicate_the_signal(
    session_factory: sessionmaker[Session],
) -> None:
    service = f"payment-{uuid.uuid4().hex[:8]}"
    event = _event(service=service)

    with session_factory() as session:
        first_id = process_alert_event(session, event, graph=GRAPH, clock=FixedClock(T0))
        session.commit()

        replayed_id = process_alert_event(session, event, graph=GRAPH, clock=FixedClock(T0))
        session.commit()

        assert replayed_id == first_id
        signals = (
            session.execute(select(IncidentSignal).where(IncidentSignal.incident_id == first_id))
            .scalars()
            .all()
        )
        assert len(signals) == 1


def test_dependency_linked_service_correlates_into_the_same_incident(
    session_factory: sessionmaker[Session],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    checkout = f"checkout-{suffix}"
    payment = f"payment-{suffix}"
    graph = ServiceDependencyGraph.from_pairs([(checkout, payment)])

    with session_factory() as session:
        payment_incident = process_alert_event(
            session, _event(service=payment), graph=graph, clock=FixedClock(T0)
        )
        session.commit()

        checkout_incident = process_alert_event(
            session,
            _event(
                service=checkout,
                alertname="CheckoutErrorRate",
                starts_at=T0 + timedelta(seconds=30),
            ),
            graph=graph,
            clock=FixedClock(T0 + timedelta(seconds=30)),
        )
        session.commit()

        assert checkout_incident == payment_incident


def test_unrelated_service_opens_a_separate_incident(
    session_factory: sessionmaker[Session],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    service_a = f"service-a-{suffix}"
    service_b = f"service-b-{suffix}"
    graph = ServiceDependencyGraph.from_pairs([])

    with session_factory() as session:
        incident_a = process_alert_event(
            session, _event(service=service_a), graph=graph, clock=FixedClock(T0)
        )
        session.commit()

        incident_b = process_alert_event(
            session, _event(service=service_b), graph=graph, clock=FixedClock(T0)
        )
        session.commit()

        assert incident_a != incident_b


def test_alert_outside_the_correlation_window_opens_a_new_incident(
    session_factory: sessionmaker[Session],
) -> None:
    service = f"payment-{uuid.uuid4().hex[:8]}"
    graph = ServiceDependencyGraph.from_pairs([])

    with session_factory() as session:
        first_id = process_alert_event(
            session, _event(service=service), graph=graph, clock=FixedClock(T0)
        )
        session.commit()

        much_later = T0 + timedelta(minutes=10)
        second_id = process_alert_event(
            session,
            _event(service=service, starts_at=much_later),
            graph=graph,
            clock=FixedClock(much_later),
        )
        session.commit()

        assert second_id != first_id
