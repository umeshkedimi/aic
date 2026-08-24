from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from aic_agents.port import ModelTier
from aic_agents.tools.base import ToolSpec
from aic_agents.tools.k8s import DeploymentHistoryInput, PodEventsInput, ServiceDependenciesInput
from aic_agents.tools.knowledge import SearchInput
from aic_agents.tools.loki import QueryRangeInput
from aic_agents.tools.prometheus import RangeQueryInput
from aic_agents.tools.registry import ToolRegistry
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import RCA, Evidence, Hypothesis, Incident, IncidentEvent, IncidentSignal
from aic_domain.enums import IncidentStatus
from aic_investigator.main import _find_next_investigating_incident_id, _poll_once
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_tables(session_factory: sessionmaker[Session]) -> None:
    """`_find_next_investigating_incident_id` scans the whole incident
    table by design (a global poller, not scoped to one test's data) — see
    the identical note in apps/aic-triage/tests/test_main.py (T6), same
    root cause: these tests need an actually-clean table, not just
    per-test-unique keys."""
    with session_factory() as session:
        session.execute(delete(IncidentEvent))
        session.execute(delete(Evidence))
        session.execute(delete(Hypothesis))
        session.execute(delete(RCA))
        session.execute(delete(IncidentSignal))
        session.execute(delete(Incident))
        session.commit()


class _FakeLLM:
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
        if agent_role == "investigation-digest":
            return response_model.model_validate({"summary": "digested", "key_facts": []})
        if agent_role == "investigation-assess":
            return response_model.model_validate({"sufficient": True, "reasoning": "enough"})
        if agent_role == "investigation-synthesize":
            return response_model.model_validate(
                {
                    "hypotheses": [
                        {
                            "statement": "the bad deploy exhausted the pool",
                            "confidence": 0.9,
                            "supporting_evidence": [],
                            "counter_evidence": [],
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected agent_role: {agent_role}")


def _fake_registry_specs() -> dict[str, ToolSpec[Any]]:
    async def _empty(_input: Any) -> Any:
        return []

    async def _prom(_input: RangeQueryInput) -> Any:
        return {"status": "success", "data": {"result": []}}

    async def _loki(_input: QueryRangeInput) -> Any:
        return {"status": "success", "data": {"result": []}}

    return {
        "prometheus.range_query": ToolSpec(
            name="prometheus.range_query",
            source="prometheus",
            input_model=RangeQueryInput,
            timeout_seconds=5.0,
            rate_limit_key="t",
            rate_limit_max_concurrency=8,
            call=_prom,
            render_query=lambda i: i.query,
        ),
        "loki.query_range": ToolSpec(
            name="loki.query_range",
            source="loki",
            input_model=QueryRangeInput,
            timeout_seconds=5.0,
            rate_limit_key="t",
            rate_limit_max_concurrency=8,
            call=_loki,
            render_query=lambda i: i.query,
        ),
        "k8s.get_deployment_history": ToolSpec(
            name="k8s.get_deployment_history",
            source="postgres",
            input_model=DeploymentHistoryInput,
            timeout_seconds=5.0,
            rate_limit_key="t",
            rate_limit_max_concurrency=8,
            call=_empty,
            render_query=lambda i: i.service,
        ),
        "k8s.get_service_dependencies": ToolSpec(
            name="k8s.get_service_dependencies",
            source="postgres",
            input_model=ServiceDependenciesInput,
            timeout_seconds=5.0,
            rate_limit_key="t",
            rate_limit_max_concurrency=8,
            call=_empty,
            render_query=lambda _i: None,
        ),
        "k8s.get_pod_events": ToolSpec(
            name="k8s.get_pod_events",
            source="k8s",
            input_model=PodEventsInput,
            timeout_seconds=5.0,
            rate_limit_key="t",
            rate_limit_max_concurrency=8,
            call=_empty,
            render_query=lambda _i: None,
        ),
        "knowledge.search": ToolSpec(
            name="knowledge.search",
            source="knowledge",
            input_model=SearchInput,
            timeout_seconds=5.0,
            rate_limit_key="t",
            rate_limit_max_concurrency=8,
            call=_empty,
            render_query=lambda i: i.query,
        ),
    }


def _make_incident(
    session: Session, *, status: IncidentStatus, created_at: datetime, with_rca: bool = False
) -> UUID:
    incident = Incident(
        fingerprint=f"payment-service:{uuid4()}",
        service="payment-service",
        environment=Environment.LOCAL,
        status=status,
        created_at=created_at,
    )
    session.add(incident)
    session.flush()
    session.add(
        IncidentSignal(
            incident_id=incident.id,
            alert_fingerprint=f"fp-{uuid4()}",
            alertname="DBPoolExhaustionPaymentService",
            service="payment-service",
            labels={},
            starts_at=created_at,
        )
    )
    if with_rca:
        session.add(
            RCA(
                incident_id=incident.id, agent_version="test", status="draft", created_at=created_at
            )
        )
    session.commit()
    incident_id: UUID = incident.id
    return incident_id


def test_find_next_investigating_incident_id_returns_none_when_nothing_to_investigate(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _make_incident(session, status=IncidentStatus.TRIAGING, created_at=T0)

    assert _find_next_investigating_incident_id(session_factory) is None


def test_find_next_investigating_incident_id_skips_incidents_that_already_have_an_rca(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        already_done = _make_incident(
            session, status=IncidentStatus.INVESTIGATING, created_at=T0, with_rca=True
        )
        still_pending = _make_incident(
            session,
            status=IncidentStatus.INVESTIGATING,
            created_at=T0 + timedelta(minutes=1),
        )

    result = _find_next_investigating_incident_id(session_factory)
    assert result == still_pending
    assert result != already_done


def test_find_next_investigating_incident_id_returns_oldest_first(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        newer = _make_incident(
            session, status=IncidentStatus.INVESTIGATING, created_at=T0 + timedelta(minutes=5)
        )
        older = _make_incident(session, status=IncidentStatus.INVESTIGATING, created_at=T0)

    result = _find_next_investigating_incident_id(session_factory)
    assert result == older
    assert result != newer


async def test_poll_once_investigates_and_persists_evidence_and_rca(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id = _make_incident(session, status=IncidentStatus.INVESTIGATING, created_at=T0)

    registry = ToolRegistry(
        specs=_fake_registry_specs(),
        _prometheus_client=httpx.AsyncClient(),
        _loki_client=httpx.AsyncClient(),
        _k8s_client=httpx.AsyncClient(),
    )

    processed = await _poll_once(
        session_factory, registry, _FakeLLM(), FixedClock(T0 + timedelta(minutes=10))
    )
    assert processed is True

    with session_factory() as session:
        evidence_rows = session.execute(
            Evidence.__table__.select().where(Evidence.incident_id == incident_id)
        ).all()
        assert len(evidence_rows) > 0
        rca_rows = session.execute(
            RCA.__table__.select().where(RCA.incident_id == incident_id)
        ).all()
        assert len(rca_rows) == 1

    await registry.aclose()


async def test_poll_once_returns_false_when_nothing_to_investigate(
    session_factory: sessionmaker[Session],
) -> None:
    registry = ToolRegistry(
        specs=_fake_registry_specs(),
        _prometheus_client=httpx.AsyncClient(),
        _loki_client=httpx.AsyncClient(),
        _k8s_client=httpx.AsyncClient(),
    )

    processed = await _poll_once(session_factory, registry, _FakeLLM(), FixedClock(T0))
    assert processed is False

    await registry.aclose()
