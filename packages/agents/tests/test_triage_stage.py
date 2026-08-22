"""Design doc §1.4 TRIAGE row (T6): severity is deterministic (tested here
without touching the fake LLM at all), title/summary generation is tested
against a real correlated-incident shape — an `Incident` in `TRIAGING` with
attached `IncidentSignal` rows, exactly what `aic_correlator.correlate`
(T4) produces — using a real Postgres, with only the LLM call itself faked
(getting a real model to emit a specific title deterministically isn't the
point of this suite; `test_litellm_contract.py` already proves structured
output round-trips through the real proxy).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from aic_agents.port import ModelTier
from aic_agents.triage import _TriageTitle, triage_incident
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_common.errors import NotFoundError
from aic_database.models import Incident, IncidentEvent, IncidentSignal
from aic_domain.enums import ActorType, IncidentStatus, IncidentTransitionEvent, Severity
from aic_domain.state_machine import IllegalTransition
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class _FakeLLM:
    def __init__(
        self, title: str = "Payment service checkout failures", summary: str = "x"
    ) -> None:
        self._title = title
        self._summary = summary
        self.calls: list[dict[str, Any]] = []

    async def complete_structured[T: BaseModel](
        self,
        *,
        tier: ModelTier,
        agent_role: str,
        system: str,
        user: str,
        response_model: type[T],
        incident_id: UUID | None = None,
    ) -> T:
        self.calls.append(
            {
                "tier": tier,
                "agent_role": agent_role,
                "system": system,
                "user": user,
                "response_model": response_model,
                "incident_id": incident_id,
            }
        )
        assert response_model is _TriageTitle
        return response_model(title=self._title, summary=self._summary)


def _make_incident(
    session: Session,
    *,
    status: IncidentStatus,
    service: str = "checkout-service",
    environment: Environment = Environment.PROD,
    signal_count: int = 2,
) -> UUID:
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        service=service,
        environment=environment,
        status=status,
        created_at=datetime.now(UTC),
    )
    session.add(incident)
    session.flush()
    for i in range(signal_count):
        session.add(
            IncidentSignal(
                incident_id=incident.id,
                alert_fingerprint=f"fp-{uuid4()}",
                alertname=f"HighLatencyPaymentService-{i}",
                service="payment-service",
                labels={"severity": "critical"},
                starts_at=datetime.now(UTC),
            )
        )
    session.commit()
    incident_id: UUID = incident.id
    return incident_id


async def test_triage_assigns_severity_title_and_transitions(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id = _make_incident(
            session, status=IncidentStatus.TRIAGING, service="checkout-service", signal_count=2
        )

        llm = _FakeLLM(title="Checkout failures from payment-service pool exhaustion")
        result = await triage_incident(
            session, incident_id, llm=llm, clock=FixedClock(datetime.now(UTC))
        )
        session.commit()

        assert result.severity == Severity.SEV2  # checkout-service, prod, 2 signals
        assert result.status == IncidentStatus.INVESTIGATING
        assert result.title == "Checkout failures from payment-service pool exhaustion"
        assert result.summary == "x"

        assert len(llm.calls) == 1
        assert llm.calls[0]["tier"] == ModelTier.CHEAP
        assert llm.calls[0]["incident_id"] == incident_id

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.severity == Severity.SEV2
        assert incident.status == IncidentStatus.INVESTIGATING

        events = list(
            session.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.seq)
            )
            .scalars()
            .all()
        )
        assert [e.event_type for e in events] == [
            "severity_assigned",
            IncidentTransitionEvent.TRIAGE_COMPLETED.value,
        ]
        assert events[0].actor_type == ActorType.SYSTEM
        assert events[0].payload["severity"] == "SEV2"
        assert events[1].actor_type == ActorType.LLM


async def test_triage_fails_fast_on_non_triaging_incident_without_calling_the_llm(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id = _make_incident(session, status=IncidentStatus.OPEN)

        llm = _FakeLLM()
        with pytest.raises(IllegalTransition):
            await triage_incident(
                session, incident_id, llm=llm, clock=FixedClock(datetime.now(UTC))
            )

        assert llm.calls == []  # no LLM spend on an incident that can't be triaged


async def test_triage_raises_not_found_for_unknown_incident(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        llm = _FakeLLM()
        with pytest.raises(NotFoundError):
            await triage_incident(session, uuid4(), llm=llm, clock=FixedClock(datetime.now(UTC)))
