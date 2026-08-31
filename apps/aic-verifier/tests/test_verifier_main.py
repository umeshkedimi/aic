from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import (
    RCA,
    Action,
    Evidence,
    ExecutionRecord,
    Hypothesis,
    Incident,
    IncidentEvent,
    IncidentSignal,
    RemediationProposal,
    VerificationRecord,
)
from aic_domain.enums import ActionStatus, ExecutionStatus, IncidentStatus
from aic_verifier.main import _find_next_verifying_incident_id, _poll_once
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    """`_find_next_verifying_incident_id` scans the whole incident table by
    design (a global poller, not scoped to one test's data) — see the
    identical note in apps/aic-triage/tests/test_main.py (T6) and
    apps/aic-investigator/tests/test_investigator_main.py (T7)."""
    with session_factory() as session:
        session.execute(delete(VerificationRecord))
        session.execute(delete(ExecutionRecord))
        session.execute(delete(Action))
        session.execute(delete(RemediationProposal))
        session.execute(delete(IncidentEvent))
        session.execute(delete(Evidence))
        session.execute(delete(Hypothesis))
        session.execute(delete(RCA))
        session.execute(delete(IncidentSignal))
        session.execute(delete(Incident))
        session.commit()


async def _no_sleep(_seconds: float) -> None:
    return None


def _make_verifying_incident(
    session: Session, *, created_at: datetime, status: IncidentStatus = IncidentStatus.VERIFYING
) -> UUID:
    service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        service=service,
        environment=Environment.PROD,
        status=status,
        created_at=created_at,
    )
    session.add(incident)
    session.flush()

    if status != IncidentStatus.VERIFYING:
        session.commit()
        incident_id: UUID = incident.id
        return incident_id

    rca = RCA(incident_id=incident.id, agent_version="test", status="draft", created_at=created_at)
    session.add(rca)
    session.flush()
    proposal = RemediationProposal(
        incident_id=incident.id, rca_id=rca.id, rationale="r", created_at=created_at
    )
    session.add(proposal)
    session.flush()
    action = Action(
        proposal_id=proposal.id,
        action_type="RollbackDeployment",
        params={"deployment": service, "from_version": "v42", "to_version": "v41"},
        target_resource=service,
        status=ActionStatus.EXECUTED.value,
        idempotency_key=f"{incident.id}:{rca.id}:RollbackDeployment",
        created_at=created_at,
    )
    session.add(action)
    session.flush()
    session.add(
        ExecutionRecord(
            action_id=action.id,
            started_at=created_at,
            finished_at=created_at,
            status=ExecutionStatus.SUCCEEDED.value,
            output={"returncode": 0},
        )
    )
    session.commit()
    incident_id = incident.id
    return incident_id


def _healthy_prometheus_client() -> httpx.AsyncClient:
    def _handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        value = "20" if "db_pool_max_size" in query else "0.0"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"result": [{"metric": {}, "value": [0, value]}]},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://prom.test")


def _empty_loki_client() -> httpx.AsyncClient:
    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://loki.test")


def test_find_next_verifying_incident_id_returns_none_when_nothing_to_verify(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _make_verifying_incident(session, created_at=T0, status=IncidentStatus.INVESTIGATING)

    assert _find_next_verifying_incident_id(session_factory) is None


def test_find_next_verifying_incident_id_skips_non_verifying(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _make_verifying_incident(session, created_at=T0, status=IncidentStatus.REMEDIATING)
        verifying = _make_verifying_incident(
            session, created_at=T0 + timedelta(minutes=1), status=IncidentStatus.VERIFYING
        )

    assert _find_next_verifying_incident_id(session_factory) == verifying


def test_find_next_verifying_incident_id_returns_oldest_first(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        newer = _make_verifying_incident(session, created_at=T0 + timedelta(minutes=5))
        older = _make_verifying_incident(session, created_at=T0)

    result = _find_next_verifying_incident_id(session_factory)
    assert result == older
    assert result != newer


async def test_poll_once_verifies_and_advances_the_incident(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id = _make_verifying_incident(session, created_at=T0)

    prom = _healthy_prometheus_client()
    loki = _empty_loki_client()

    processed = await _poll_once(session_factory, prom, loki, FixedClock(T0), 90.0, sleep=_no_sleep)
    assert processed is True

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.RESOLVED

    # A second poll must not re-pick the now-resolved incident.
    processed_again = await _poll_once(
        session_factory, prom, loki, FixedClock(T0), 90.0, sleep=_no_sleep
    )
    assert processed_again is False

    await prom.aclose()
    await loki.aclose()


async def test_poll_once_returns_false_when_nothing_to_verify(
    session_factory: sessionmaker[Session],
) -> None:
    prom = _healthy_prometheus_client()
    loki = _empty_loki_client()

    processed = await _poll_once(session_factory, prom, loki, FixedClock(T0), 90.0, sleep=_no_sleep)
    assert processed is False

    await prom.aclose()
    await loki.aclose()
