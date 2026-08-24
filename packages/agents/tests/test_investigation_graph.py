from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from aic_agents.graphs.investigation import (
    EvidenceRecord,
    LineOfInquiry,
    RCAResult,
    assess,
    build_graph,
    digest_one,
    gather,
    plan,
    run_investigation,
    self_check,
    synthesize,
)
from aic_agents.port import LLMStructuredOutputError, ModelTier
from aic_agents.tools.base import ToolSpec
from aic_agents.tools.k8s import DeploymentHistoryInput, PodEventsInput, ServiceDependenciesInput
from aic_agents.tools.knowledge import SearchInput
from aic_agents.tools.loki import QueryRangeInput
from aic_agents.tools.prometheus import RangeQueryInput
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_database.models import RCA as RCARow
from aic_database.models import Evidence as EvidenceRow
from aic_database.models import Hypothesis as HypothesisRow
from aic_database.models import Incident as IncidentRow
from aic_database.models import IncidentSignal as IncidentSignalRow
from aic_domain.enums import EvidenceStatus, IncidentStatus
from aic_domain.models import Hypothesis
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)

ALL_TOOL_NAMES = {
    "prometheus.range_query",
    "loki.query_range",
    "k8s.get_deployment_history",
    "k8s.get_service_dependencies",
    "k8s.get_pod_events",
    "knowledge.search",
}


class _ScriptedLLM:
    """Fakes `LLMPort` for graph/node tests, branching on `agent_role` —
    the same "fake the LLM seam, keep everything else real" split T5/T6
    established."""

    def __init__(
        self,
        *,
        assess_sufficient: bool = True,
        synth_payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._assess_sufficient = assess_sufficient
        self._synth_payloads = list(
            synth_payloads
            or [
                {
                    "hypotheses": [
                        {
                            "statement": "the bad deploy exhausted the connection pool",
                            "confidence": 0.9,
                            "supporting_evidence": [],
                            "counter_evidence": [],
                        }
                    ]
                }
            ]
        )

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
        self.calls.append({"agent_role": agent_role, "tier": tier})
        if agent_role == "investigation-digest":
            return response_model.model_validate({"summary": "digested", "key_facts": []})
        if agent_role == "investigation-assess":
            return response_model.model_validate(
                {"sufficient": self._assess_sufficient, "reasoning": "because"}
            )
        if agent_role == "investigation-synthesize":
            payload = self._synth_payloads[
                min(len(self._synth_payloads) - 1, self._synth_attempt())
            ]
            return response_model.model_validate(payload)
        raise AssertionError(f"unexpected agent_role: {agent_role}")

    def _synth_attempt(self) -> int:
        return sum(1 for c in self.calls if c["agent_role"] == "investigation-synthesize") - 1


def _fake_tools(*, deployment_data: list[dict[str, Any]] | None = None) -> dict[str, ToolSpec[Any]]:
    async def _prom(_input: RangeQueryInput) -> Any:
        return {"status": "success", "data": {"result": []}}

    async def _loki(_input: QueryRangeInput) -> Any:
        return {"status": "success", "data": {"result": []}}

    async def _deploy_history(_input: DeploymentHistoryInput) -> Any:
        return deployment_data if deployment_data is not None else []

    async def _svc_deps(_input: ServiceDependenciesInput) -> Any:
        return []

    async def _pod_events(_input: PodEventsInput) -> Any:
        return []

    async def _knowledge(_input: SearchInput) -> Any:
        return []

    return {
        "prometheus.range_query": ToolSpec(
            name="prometheus.range_query",
            source="prometheus",
            input_model=RangeQueryInput,
            timeout_seconds=5.0,
            rate_limit_key="test-prom",
            rate_limit_max_concurrency=8,
            call=_prom,
            render_query=lambda i: i.query,
        ),
        "loki.query_range": ToolSpec(
            name="loki.query_range",
            source="loki",
            input_model=QueryRangeInput,
            timeout_seconds=5.0,
            rate_limit_key="test-loki",
            rate_limit_max_concurrency=8,
            call=_loki,
            render_query=lambda i: i.query,
        ),
        "k8s.get_deployment_history": ToolSpec(
            name="k8s.get_deployment_history",
            source="postgres",
            input_model=DeploymentHistoryInput,
            timeout_seconds=5.0,
            rate_limit_key="test-pg",
            rate_limit_max_concurrency=8,
            call=_deploy_history,
            render_query=lambda i: i.service,
        ),
        "k8s.get_service_dependencies": ToolSpec(
            name="k8s.get_service_dependencies",
            source="postgres",
            input_model=ServiceDependenciesInput,
            timeout_seconds=5.0,
            rate_limit_key="test-pg",
            rate_limit_max_concurrency=8,
            call=_svc_deps,
            render_query=lambda _i: None,
        ),
        "k8s.get_pod_events": ToolSpec(
            name="k8s.get_pod_events",
            source="k8s",
            input_model=PodEventsInput,
            timeout_seconds=5.0,
            rate_limit_key="test-k8s",
            rate_limit_max_concurrency=8,
            call=_pod_events,
            render_query=lambda _i: None,
        ),
        "knowledge.search": ToolSpec(
            name="knowledge.search",
            source="knowledge",
            input_model=SearchInput,
            timeout_seconds=5.0,
            rate_limit_key="test-knowledge",
            rate_limit_max_concurrency=8,
            call=_knowledge,
            render_query=lambda i: i.query,
        ),
    }


def _seed_incident(session_factory: sessionmaker[Session], *, starts_at: datetime = T0) -> UUID:
    with session_factory() as session:
        incident = IncidentRow(
            fingerprint=f"payment-service:{uuid4()}",
            service="payment-service",
            environment=Environment.LOCAL,
            status=IncidentStatus.INVESTIGATING,
            created_at=starts_at,
        )
        session.add(incident)
        session.flush()
        session.add(
            IncidentSignalRow(
                incident_id=incident.id,
                alert_fingerprint=f"fp-{uuid4()}",
                alertname="DBPoolExhaustionPaymentService",
                service="payment-service",
                labels={},
                starts_at=starts_at,
            )
        )
        session.commit()
        incident_id: UUID = incident.id
        return incident_id


def test_plan_produces_fixed_lines_of_inquiry_covering_every_tool() -> None:
    lines = plan(service="payment-service", window_start=T0, window_end=T0 + timedelta(minutes=10))

    assert {line.tool for line in lines} == ALL_TOOL_NAMES
    prom_lines = [line for line in lines if line.tool == "prometheus.range_query"]
    assert len(prom_lines) == 3


async def test_gather_persists_evidence_rows_for_ok_and_error_results(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _seed_incident(session_factory)

    async def _ok(_input: RangeQueryInput) -> Any:
        return {"result": []}

    async def _fail(_input: QueryRangeInput) -> Any:
        raise RuntimeError("loki unreachable")

    tools: dict[str, ToolSpec[Any]] = {
        "prometheus.range_query": ToolSpec(
            name="prometheus.range_query",
            source="prometheus",
            input_model=RangeQueryInput,
            timeout_seconds=1.0,
            rate_limit_key="g1",
            rate_limit_max_concurrency=4,
            call=_ok,
            render_query=lambda i: i.query,
        ),
        "loki.query_range": ToolSpec(
            name="loki.query_range",
            source="loki",
            input_model=QueryRangeInput,
            timeout_seconds=1.0,
            rate_limit_key="g1",
            rate_limit_max_concurrency=4,
            call=_fail,
            render_query=lambda i: i.query,
        ),
    }
    lines = [
        LineOfInquiry(
            tool="prometheus.range_query",
            params={"query": "up", "start": T0, "end": T0},
            rationale="r",
        ),
        LineOfInquiry(
            tool="loki.query_range", params={"query": "{}", "start": T0, "end": T0}, rationale="r"
        ),
    ]

    records = await gather(
        session_factory=session_factory,
        incident_id=incident_id,
        lines_of_inquiry=lines,
        tools=tools,
        clock=FixedClock(T0),
    )

    statuses = {r.tool: r.status for r in records}
    assert statuses["prometheus.range_query"] == EvidenceStatus.OK
    assert statuses["loki.query_range"] == EvidenceStatus.ERROR

    with session_factory() as session:
        rows = (
            session.execute(select(EvidenceRow).where(EvidenceRow.incident_id == incident_id))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        by_tool = {row.tool: row for row in rows}
        assert by_tool["loki.query_range"].status == EvidenceStatus.ERROR
        assert "loki unreachable" in (by_tool["loki.query_range"].result_digest or "")


async def test_digest_skips_the_llm_for_error_evidence() -> None:
    llm = _ScriptedLLM()
    record = EvidenceRecord(
        evidence_id=uuid4(),
        tool="loki.query_range",
        status=EvidenceStatus.ERROR,
        error_message="timeout",
    )

    digest = await digest_one(llm=llm, incident_id=uuid4(), record=record)

    assert llm.calls == []
    assert "timeout" in digest.summary


async def test_digest_calls_the_cheap_tier_llm_for_ok_evidence() -> None:
    llm = _ScriptedLLM()
    record = EvidenceRecord(
        evidence_id=uuid4(),
        tool="prometheus.range_query",
        status=EvidenceStatus.OK,
        data={"result": []},
    )

    digest = await digest_one(llm=llm, incident_id=uuid4(), record=record)

    assert digest.summary == "digested"
    assert llm.calls == [{"agent_role": "investigation-digest", "tier": ModelTier.CHEAP}]


async def test_assess_short_circuits_at_the_iteration_cap_without_calling_the_llm() -> None:
    llm = _ScriptedLLM(assess_sufficient=False)

    sufficient = await assess(llm=llm, incident_id=uuid4(), digests=[], iteration=3)

    assert sufficient is True
    assert llm.calls == []


async def test_assess_calls_the_llm_below_the_iteration_cap() -> None:
    llm = _ScriptedLLM(assess_sufficient=False)

    sufficient = await assess(llm=llm, incident_id=uuid4(), digests=[], iteration=1)

    assert sufficient is False
    assert len(llm.calls) == 1


async def test_synthesize_persists_rca_and_hypotheses_citing_real_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _seed_incident(session_factory)
    evidence_id = uuid4()
    digests = [
        {
            "evidence_id": evidence_id,
            "tool": "k8s.get_deployment_history",
            "status": EvidenceStatus.OK,
            "summary": "deployed v42",
            "key_facts": [],
        }
    ]
    from aic_agents.graphs.investigation import EvidenceDigest

    llm = _ScriptedLLM(
        synth_payloads=[
            {
                "hypotheses": [
                    {
                        "statement": "the v42 deploy exhausted the pool",
                        "confidence": 0.9,
                        "supporting_evidence": [str(evidence_id)],
                        "counter_evidence": [],
                    }
                ]
            }
        ]
    )

    result = await synthesize(
        session_factory=session_factory,
        llm=llm,
        clock=FixedClock(T0),
        incident_id=incident_id,
        digests=[EvidenceDigest.model_validate(d) for d in digests],
    )

    assert isinstance(result, RCAResult)
    assert len(result.hypotheses) == 1
    assert result.hypotheses[0].evidence_ids == [evidence_id]

    with session_factory() as session:
        rca_row = session.get(RCARow, result.rca_id)
        assert rca_row is not None
        hyp_rows = (
            session.execute(select(HypothesisRow).where(HypothesisRow.rca_id == result.rca_id))
            .scalars()
            .all()
        )
        assert len(hyp_rows) == 1


async def test_synthesize_retries_once_on_a_nonexistent_evidence_citation(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _seed_incident(session_factory)
    real_evidence_id = uuid4()
    fake_evidence_id = uuid4()
    from aic_agents.graphs.investigation import EvidenceDigest

    llm = _ScriptedLLM(
        synth_payloads=[
            {
                "hypotheses": [
                    {
                        "statement": "bad citation",
                        "confidence": 0.5,
                        "supporting_evidence": [str(fake_evidence_id)],
                        "counter_evidence": [],
                    }
                ]
            },
            {
                "hypotheses": [
                    {
                        "statement": "corrected citation",
                        "confidence": 0.7,
                        "supporting_evidence": [str(real_evidence_id)],
                        "counter_evidence": [],
                    }
                ]
            },
        ]
    )
    digest = EvidenceDigest(
        evidence_id=real_evidence_id,
        tool="k8s.get_deployment_history",
        status=EvidenceStatus.OK,
        summary="deployed v42",
    )

    result = await synthesize(
        session_factory=session_factory,
        llm=llm,
        clock=FixedClock(T0),
        incident_id=incident_id,
        digests=[digest],
    )

    assert len(llm.calls) == 2
    assert result.hypotheses[0].statement == "corrected citation"


async def test_synthesize_raises_after_exhausting_citation_retries(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _seed_incident(session_factory)
    fake_evidence_id = uuid4()
    from aic_agents.graphs.investigation import EvidenceDigest

    always_bad = {
        "hypotheses": [
            {
                "statement": "always cites a fake id",
                "confidence": 0.5,
                "supporting_evidence": [str(fake_evidence_id)],
                "counter_evidence": [],
            }
        ]
    }
    llm = _ScriptedLLM(synth_payloads=[always_bad, always_bad])
    digest = EvidenceDigest(
        evidence_id=uuid4(),
        tool="k8s.get_deployment_history",
        status=EvidenceStatus.OK,
        summary="deployed v42",
    )

    try:
        await synthesize(
            session_factory=session_factory,
            llm=llm,
            clock=FixedClock(T0),
            incident_id=incident_id,
            digests=[digest],
        )
        raise AssertionError("expected LLMStructuredOutputError")
    except LLMStructuredOutputError:
        pass

    assert len(llm.calls) == 2


async def test_self_check_demotes_a_hypothesis_with_a_timeline_contradiction(
    session_factory: sessionmaker[Session],
) -> None:
    """The explicit "Done when" requirement: deliberately inject a timeline
    contradiction (a deploy cited as root cause that happened *after*
    symptom onset) and prove self-check catches it."""
    incident_id = _seed_incident(session_factory)
    evidence_id = uuid4()
    symptom_onset_at = T0

    with session_factory() as session:
        rca_row = RCARow(
            incident_id=incident_id, agent_version="test", status="draft", created_at=T0
        )
        session.add(rca_row)
        session.flush()
        rca_id = rca_row.id
        hyp_row = HypothesisRow(
            id=uuid4(),
            rca_id=rca_id,
            rank=1,
            statement="a deploy that happened after the symptom caused it",
            confidence=0.9,
            evidence_ids=[str(evidence_id)],
            counter_evidence=[],
        )
        session.add(hyp_row)
        session.commit()
        hyp_id = hyp_row.id

    top = Hypothesis(
        id=hyp_id,
        rca_id=rca_id,
        rank=1,
        statement="a deploy that happened after the symptom caused it",
        confidence=0.9,
        evidence_ids=[evidence_id],
        counter_evidence=[],
    )
    rca_result = RCAResult(rca_id=rca_id, incident_id=incident_id, hypotheses=[top])
    deploy_after_onset = symptom_onset_at + timedelta(minutes=5)
    records = [
        EvidenceRecord(
            evidence_id=evidence_id,
            tool="k8s.get_deployment_history",
            status=EvidenceStatus.OK,
            data=[{"deployed_at": deploy_after_onset.isoformat()}],
        )
    ]

    final = await self_check(
        session_factory=session_factory,
        rca_result=rca_result,
        evidence_records=records,
        symptom_onset_at=symptom_onset_at,
    )

    assert final.hypotheses[0].demoted_reason is not None
    assert "timeline contradiction" in final.hypotheses[0].demoted_reason

    with session_factory() as session:
        row = session.get(HypothesisRow, hyp_id)
        assert row is not None
        assert row.demoted_reason is not None


async def test_self_check_leaves_a_consistent_hypothesis_untouched(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _seed_incident(session_factory)
    evidence_id = uuid4()
    symptom_onset_at = T0
    deploy_before_onset = symptom_onset_at - timedelta(minutes=5)

    top = Hypothesis(
        id=uuid4(),
        rca_id=uuid4(),
        rank=1,
        statement="consistent hypothesis",
        confidence=0.9,
        evidence_ids=[evidence_id],
        counter_evidence=[],
    )
    rca_result = RCAResult(rca_id=uuid4(), incident_id=incident_id, hypotheses=[top])
    records = [
        EvidenceRecord(
            evidence_id=evidence_id,
            tool="k8s.get_deployment_history",
            status=EvidenceStatus.OK,
            data=[{"deployed_at": deploy_before_onset.isoformat()}],
        )
    ]

    final = await self_check(
        session_factory=session_factory,
        rca_result=rca_result,
        evidence_records=records,
        symptom_onset_at=symptom_onset_at,
    )

    assert final.hypotheses[0].demoted_reason is None


async def test_graph_assess_loop_terminates_at_the_three_iteration_cap(
    session_factory: sessionmaker[Session],
) -> None:
    """The explicit "Done when" requirement: the assess loop must actually
    stop at the iteration cap rather than looping forever."""
    incident_id = _seed_incident(session_factory)
    tools = _fake_tools()
    llm = _ScriptedLLM(assess_sufficient=False)  # never satisfied -> always loops

    compiled = build_graph(
        tools=tools, llm=llm, session_factory=session_factory, clock=FixedClock(T0)
    )
    initial_state: dict[str, Any] = {
        "incident_id": incident_id,
        "service": "payment-service",
        "window_start": T0,
        "window_end": T0 + timedelta(minutes=10),
        "symptom_onset_at": T0,
        "iteration": 0,
        "lines_of_inquiry": [],
        "evidence_records": [],
        "new_evidence_records": [],
        "digests": [],
        "assess_sufficient": False,
        "rca_result": None,
    }

    final_state = await asyncio.wait_for(compiled.ainvoke(initial_state), timeout=15)

    assert final_state["iteration"] == 3
    lines_per_plan = len(plan(service="payment-service", window_start=T0, window_end=T0))
    assert len(final_state["evidence_records"]) == lines_per_plan * 3
    assert final_state["rca_result"] is not None
    # Only 2 real LLM calls: the 3rd iteration's assess short-circuits to
    # "sufficient" deterministically once the budget is exhausted, per
    # `assess()`'s own iteration-cap check — no LLM call wasted on a
    # decision that's already forced.
    assess_calls = [c for c in llm.calls if c["agent_role"] == "investigation-assess"]
    assert len(assess_calls) == 2


async def test_run_investigation_produces_an_rca_result_citing_real_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _seed_incident(session_factory)
    tools = _fake_tools(
        deployment_data=[{"deployed_at": (T0 - timedelta(minutes=5)).isoformat(), "version": "v42"}]
    )
    llm = _ScriptedLLM(assess_sufficient=True)

    result = await run_investigation(
        session_factory=session_factory,
        incident_id=incident_id,
        tools=tools,
        llm=llm,
        clock=FixedClock(T0 + timedelta(minutes=10)),
    )

    assert isinstance(result, RCAResult)
    assert result.incident_id == incident_id
    assert len(result.hypotheses) >= 1

    with session_factory() as session:
        evidence_rows = (
            session.execute(select(EvidenceRow).where(EvidenceRow.incident_id == incident_id))
            .scalars()
            .all()
        )
        assert len(evidence_rows) == len(
            plan(service="payment-service", window_start=T0, window_end=T0)
        )
        rca_row = session.get(RCARow, result.rca_id)
        assert rca_row is not None
