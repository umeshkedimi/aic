"""Verification & resolution (design doc §1.12, T11): a real
Incident -> RCA -> RemediationProposal -> Action -> ExecutionRecord chain
(same reasoning as T9/T10's tests — pass/fail, the retry bound, and the
resulting incident transitions are only real when proven against a real
database), with fake Prometheus/Loki `httpx.MockTransport`s standing in
for the real HTTP calls and an injected `sleep` so these tests don't
actually wait 90 seconds. Real-cluster behavior (an actual soak against a
real Prometheus/Loki) is verified separately, by hand, against a live kind
cluster — the same precedent T7/T10's own live-cluster notes set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from aic_agents.verification import verify_incident
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_common.errors import IllegalStateError, NotFoundError
from aic_database.models import (
    RCA,
    Action,
    ExecutionRecord,
    Incident,
    IncidentEvent,
    RemediationProposal,
    VerificationRecord,
)
from aic_domain.enums import ActionStatus, ExecutionStatus, IncidentStatus
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)


async def _no_sleep(_seconds: float) -> None:
    return None


def _seed_verifying_incident(
    session: Session,
    *,
    execution_status: str = ExecutionStatus.SUCCEEDED.value,
) -> tuple[UUID, UUID]:
    """Seed a real Incident(VERIFYING) -> RCA -> RemediationProposal ->
    Action(EXECUTED) -> ExecutionRecord chain and return
    (incident_id, execution_id)."""
    service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"{service}:{uuid4()}",
        service=service,
        environment=Environment.PROD,
        status=IncidentStatus.VERIFYING,
        created_at=T0,
    )
    session.add(incident)
    session.flush()

    rca = RCA(incident_id=incident.id, agent_version="test", status="draft", created_at=T0)
    session.add(rca)
    session.flush()

    proposal = RemediationProposal(
        incident_id=incident.id, rca_id=rca.id, rationale="r", created_at=T0
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
        created_at=T0,
    )
    session.add(action)
    session.flush()

    execution = ExecutionRecord(
        action_id=action.id,
        started_at=T0,
        finished_at=T0,
        status=execution_status,
        output={"returncode": 0},
    )
    session.add(execution)
    session.commit()
    return incident.id, execution.id


def _prometheus_client(
    *, latency: float, error_rate: float, in_use: float, max_size: float
) -> httpx.AsyncClient:
    def _handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        if "histogram_quantile" in query:
            value = latency
        elif "http_requests_total" in query:
            value = error_rate
        elif "db_pool_max_size" in query:
            value = max_size
        elif "db_pool_connections_in_use" in query:
            value = in_use
        else:
            raise AssertionError(f"unexpected query: {query}")
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"result": [{"metric": {}, "value": [0, str(value)]}]},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://prom.test")


def _loki_client(*, count: int = 0) -> httpx.AsyncClient:
    def _handler(_request: httpx.Request) -> httpx.Response:
        values = [[str(i), "line"] for i in range(count)]
        result = [{"stream": {}, "values": values}] if count else []
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://loki.test")


def _event_types(session: Session, incident_id: UUID) -> list[str]:
    events = list(
        session.execute(
            select(IncidentEvent)
            .where(IncidentEvent.incident_id == incident_id)
            .order_by(IncidentEvent.seq)
        )
        .scalars()
        .all()
    )
    return [e.event_type for e in events]


async def test_verify_incident_passes_and_resolves(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        incident_id, execution_id = _seed_verifying_incident(session)

    prom = _prometheus_client(latency=0.1, error_rate=0.0, in_use=3, max_size=20)
    loki = _loki_client(count=0)
    record = await verify_incident(
        session_factory=session_factory,
        incident_id=incident_id,
        prometheus_client=prom,
        loki_client=loki,
        clock=FixedClock(T0),
        soak_seconds=90.0,
        sleep=_no_sleep,
    )
    await prom.aclose()
    await loki.aclose()

    assert record.execution_id == execution_id
    assert record.passed is True
    assert record.metric_snapshots["latency"]["passed"] is True
    assert record.metric_snapshots["pool_exhaustion"]["passed"] is True

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.RESOLVED
        assert incident.resolved_at is not None
        assert _event_types(session, incident_id) == ["verification_passed", "soak_passed"]


async def test_verify_incident_fails_loops_back_to_investigating_on_first_failure(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _execution_id = _seed_verifying_incident(session)

    prom = _prometheus_client(latency=2.5, error_rate=0.3, in_use=3, max_size=3)
    loki = _loki_client(count=5)
    record = await verify_incident(
        session_factory=session_factory,
        incident_id=incident_id,
        prometheus_client=prom,
        loki_client=loki,
        clock=FixedClock(T0),
        soak_seconds=90.0,
        sleep=_no_sleep,
    )
    await prom.aclose()
    await loki.aclose()

    assert record.passed is False
    assert record.metric_snapshots["recent_log_count"] == 5

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.INVESTIGATING
        assert incident.resolved_at is None
        assert _event_types(session, incident_id) == ["verification_failed", "verification_failed"]
        evidence_rows = list(
            session.execute(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id))
            .scalars()
            .all()
        )
        assert len(evidence_rows) == 2


async def test_verify_incident_escalates_on_second_failure(
    session_factory: sessionmaker[Session],
) -> None:
    """The one-retry bound (§6/§1.12): a second failed verification for the
    same incident (a second execution after the loop-back) must escalate,
    not loop back again."""
    with session_factory() as session:
        incident_id, first_execution_id = _seed_verifying_incident(session)

    prom_fail = _prometheus_client(latency=2.5, error_rate=0.3, in_use=3, max_size=3)
    loki = _loki_client(count=1)
    first = await verify_incident(
        session_factory=session_factory,
        incident_id=incident_id,
        prometheus_client=prom_fail,
        loki_client=loki,
        clock=FixedClock(T0),
        soak_seconds=90.0,
        sleep=_no_sleep,
    )
    assert first.passed is False

    # Simulate a second remediation attempt: incident back to VERIFYING
    # with a second Action/ExecutionRecord, per a real retry cycle.
    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        incident.status = IncidentStatus.VERIFYING
        rca2 = RCA(incident_id=incident_id, agent_version="test2", status="draft", created_at=T0)
        session.add(rca2)
        session.flush()
        proposal2 = RemediationProposal(
            incident_id=incident_id, rca_id=rca2.id, rationale="r2", created_at=T0
        )
        session.add(proposal2)
        session.flush()
        action2 = Action(
            proposal_id=proposal2.id,
            action_type="RollbackDeployment",
            params={"deployment": "x", "from_version": "v42", "to_version": "v41"},
            target_resource="x",
            status=ActionStatus.EXECUTED.value,
            idempotency_key=f"{incident_id}:{rca2.id}:RollbackDeployment",
            created_at=T0,
        )
        session.add(action2)
        session.flush()
        execution2 = ExecutionRecord(
            action_id=action2.id,
            started_at=T0 + timedelta(minutes=5),
            finished_at=T0 + timedelta(minutes=5),
            status=ExecutionStatus.SUCCEEDED.value,
            output={"returncode": 0},
        )
        session.add(execution2)
        session.commit()
        second_execution_id = execution2.id

    second = await verify_incident(
        session_factory=session_factory,
        incident_id=incident_id,
        prometheus_client=prom_fail,
        loki_client=loki,
        clock=FixedClock(T0),
        soak_seconds=90.0,
        sleep=_no_sleep,
    )
    await prom_fail.aclose()
    await loki.aclose()

    assert second.execution_id == second_execution_id
    assert second.execution_id != first_execution_id
    assert second.passed is False

    with session_factory() as session:
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.ESCALATED


async def test_verify_incident_is_idempotent_on_retry(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, execution_id = _seed_verifying_incident(session)

    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"result": [{"metric": {}, "value": [0, "0.1"]}]},
            },
        )

    prom = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://prom.test")
    loki = _loki_client(count=0)

    first = await verify_incident(
        session_factory=session_factory,
        incident_id=incident_id,
        prometheus_client=prom,
        loki_client=loki,
        clock=FixedClock(T0),
        soak_seconds=90.0,
        sleep=_no_sleep,
    )
    calls_after_first = calls

    second = await verify_incident(
        session_factory=session_factory,
        incident_id=incident_id,
        prometheus_client=prom,
        loki_client=loki,
        clock=FixedClock(T0),
        soak_seconds=90.0,
        sleep=_no_sleep,
    )
    await prom.aclose()
    await loki.aclose()

    assert first.id == second.id
    assert calls == calls_after_first  # no new Prometheus calls on the retry

    with session_factory() as session:
        records = list(
            session.execute(
                select(VerificationRecord).where(VerificationRecord.execution_id == execution_id)
            )
            .scalars()
            .all()
        )
        assert len(records) == 1


async def test_verify_incident_rejects_an_incident_that_is_not_verifying(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _execution_id = _seed_verifying_incident(session)
        incident = session.get(Incident, incident_id)
        assert incident is not None
        incident.status = IncidentStatus.INVESTIGATING
        session.commit()

    prom = _prometheus_client(latency=0.1, error_rate=0.0, in_use=3, max_size=20)
    loki = _loki_client()
    with pytest.raises(IllegalStateError):
        await verify_incident(
            session_factory=session_factory,
            incident_id=incident_id,
            prometheus_client=prom,
            loki_client=loki,
            clock=FixedClock(T0),
            soak_seconds=90.0,
            sleep=_no_sleep,
        )
    await prom.aclose()
    await loki.aclose()


async def test_verify_incident_raises_not_found_for_unknown_incident(
    session_factory: sessionmaker[Session],
) -> None:
    prom = _prometheus_client(latency=0.1, error_rate=0.0, in_use=3, max_size=20)
    loki = _loki_client()
    with pytest.raises(NotFoundError):
        await verify_incident(
            session_factory=session_factory,
            incident_id=uuid4(),
            prometheus_client=prom,
            loki_client=loki,
            clock=FixedClock(T0),
            soak_seconds=90.0,
            sleep=_no_sleep,
        )
    await prom.aclose()
    await loki.aclose()


async def test_verify_incident_missing_metric_data_treated_as_passing(
    session_factory: sessionmaker[Session],
) -> None:
    """Absence of a Prometheus series (empty vector) is what a healthy,
    quiet service also looks like — treated as "not breaching", per the
    module's own documented reasoning, not as a failure."""
    with session_factory() as session:
        incident_id, _execution_id = _seed_verifying_incident(session)

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    prom = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://prom.test")
    loki = _loki_client()
    record = await verify_incident(
        session_factory=session_factory,
        incident_id=incident_id,
        prometheus_client=prom,
        loki_client=loki,
        clock=FixedClock(T0),
        soak_seconds=90.0,
        sleep=_no_sleep,
    )
    await prom.aclose()
    await loki.aclose()

    assert record.passed is True
