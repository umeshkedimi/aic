"""Design doc §1.4 PLAN REMEDIATION / APPLY POLICY rows (T8).

Candidate construction and policy application are tested against a real
Postgres shape — a real correlated `Incident` + a real `RCA`/`Hypothesis`
citing a real `k8s.get_deployment_history` `Evidence` row + real
`Deployment` history rows, exactly what T7's investigation graph and
`apps/toy-ops`'s deploy script produce — with only the remediation-choice
LLM call faked (deterministic bookkeeping is this suite's job; real
structured-output round-tripping is `test_litellm_contract.py`'s job, T5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from aic_agents.port import ModelTier
from aic_agents.remediation import (
    NoRemediationCandidateError,
    RemediationChoiceError,
    _RemediationChoice,
    _select_candidate,
    plan_remediation,
)
from aic_common.clock import FixedClock
from aic_common.config import Environment
from aic_common.errors import IllegalStateError, NotFoundError
from aic_database.models import (
    RCA,
    ApprovalRequest,
    Deployment,
    Evidence,
    Hypothesis,
    Incident,
    IncidentEvent,
)
from aic_domain.actions import (
    ActionCandidate,
    ConfigChange,
    PatchConfigParams,
    RollbackDeploymentParams,
)
from aic_domain.enums import ActionStatus, ActionType, EvidenceStatus, IncidentStatus, PolicyEffect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class _FakeLLM:
    def __init__(self, chosen_action_type: ActionType, rationale: str = "because evidence") -> None:
        self._chosen_action_type = chosen_action_type
        self._rationale = rationale
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
        self.calls.append({"tier": tier, "agent_role": agent_role, "user": user})
        assert response_model is _RemediationChoice
        return response_model(
            chosen_action_type=self._chosen_action_type, rationale=self._rationale
        )


def _seed_investigation(
    session: Session,
    *,
    environment: Environment = Environment.PROD,
    service: str | None = None,
    bad_pool_size: int = 3,
    good_pool_size: int = 20,
    with_previous_deploy: bool = True,
    cite_deployment_evidence: bool = True,
) -> tuple[UUID, str]:
    # `Deployment` has no incident_id column (§5: it's real deploy-time
    # data, not scoped to an incident) and this suite shares one
    # session-scoped Postgres container across tests (see conftest.py) —
    # a fixed service name would let one test's deployment rows leak into
    # another's `service=` deployment-history query. A unique service name
    # per call gives each test its own slice of the table instead.
    if service is None:
        service = f"payment-service-{uuid4().hex[:8]}"
    incident = Incident(
        fingerprint=f"checkout-service:{uuid4()}",
        service="checkout-service",
        environment=environment,
        status=IncidentStatus.INVESTIGATING,
        created_at=datetime.now(UTC),
    )
    session.add(incident)
    session.flush()

    deployed_at_bad = datetime.now(UTC)
    if with_previous_deploy:
        session.add(
            Deployment(
                service=service,
                version="v41",
                image_tag="v41",
                config_diff={},
                deployed_at=deployed_at_bad - timedelta(hours=1),
                deployed_by="test",
            )
        )
    session.add(
        Deployment(
            service=service,
            version="v42",
            image_tag="v42",
            config_diff={"DB_POOL_SIZE": {"from": good_pool_size, "to": bad_pool_size}},
            deployed_at=deployed_at_bad,
            deployed_by="test",
        )
    )
    session.flush()

    evidence = Evidence(
        incident_id=incident.id,
        source="postgres",
        tool="k8s.get_deployment_history" if cite_deployment_evidence else "prometheus.range_query",
        query=f"service={service}" if cite_deployment_evidence else "some other query",
        result_digest="[]",
        collected_at=datetime.now(UTC),
        status=EvidenceStatus.OK,
    )
    session.add(evidence)
    session.flush()

    rca = RCA(
        incident_id=incident.id,
        agent_version="test",
        status="draft",
        created_at=datetime.now(UTC),
    )
    session.add(rca)
    session.flush()

    session.add(
        Hypothesis(
            rca_id=rca.id,
            rank=1,
            statement="The bad deploy exhausted the DB pool",
            confidence=0.9,
            evidence_ids=[str(evidence.id)],
            counter_evidence=[],
        )
    )
    session.commit()
    incident_id: UUID = incident.id
    return incident_id, service


async def test_plan_remediation_proposes_rollback_given_a_real_rca(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, service = _seed_investigation(session)
        llm = _FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT, rationale="rollback wins")
        action = await plan_remediation(
            session, incident_id, llm=llm, clock=FixedClock(datetime.now(UTC))
        )
        session.commit()

        assert action.action_type == ActionType.ROLLBACK_DEPLOYMENT.value
        assert action.target_resource == service
        assert action.params["from_version"] == "v42"
        assert action.params["to_version"] == "v41"
        assert action.policy_decision == PolicyEffect.REQUIRE_APPROVAL
        assert action.status == ActionStatus.PENDING_APPROVAL.value
        assert len(llm.calls) == 1

        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.AWAITING_APPROVAL


async def test_plan_remediation_proposes_patch_config_when_llm_chooses_it(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _service = _seed_investigation(session)
        llm = _FakeLLM(chosen_action_type=ActionType.PATCH_CONFIG, rationale="patch wins")
        action = await plan_remediation(
            session, incident_id, llm=llm, clock=FixedClock(datetime.now(UTC))
        )
        session.commit()

        assert action.action_type == ActionType.PATCH_CONFIG.value
        assert action.params["changes"] == [
            {"key": "DB_POOL_SIZE", "from_value": "3", "to_value": "20"}
        ]


async def test_policy_effect_differs_by_environment_via_the_real_rule_table(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        prod_incident_id, _ = _seed_investigation(session, environment=Environment.PROD)
        staging_incident_id, _ = _seed_investigation(session, environment=Environment.STAGING)

        prod_action = await plan_remediation(
            session,
            prod_incident_id,
            llm=_FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT),
            clock=FixedClock(datetime.now(UTC)),
        )
        staging_action = await plan_remediation(
            session,
            staging_incident_id,
            llm=_FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT),
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

        assert prod_action.policy_decision == PolicyEffect.REQUIRE_APPROVAL
        assert prod_action.status == ActionStatus.PENDING_APPROVAL.value
        assert staging_action.policy_decision == PolicyEffect.AUTO_APPROVE
        assert staging_action.status == ActionStatus.APPROVED.value

        prod_incident = session.get(Incident, prod_incident_id)
        staging_incident = session.get(Incident, staging_incident_id)
        assert prod_incident is not None
        assert staging_incident is not None
        assert prod_incident.status == IncidentStatus.AWAITING_APPROVAL
        assert staging_incident.status == IncidentStatus.REMEDIATING

        prod_request = session.execute(
            select(ApprovalRequest).where(ApprovalRequest.action_id == prod_action.id)
        ).scalar_one()
        assert prod_request.quorum == 1
        assert prod_request.required_roles == ["sre"]
        assert prod_request.status == "pending"
        assert prod_request.expires_at > prod_incident.created_at

        staging_request = session.execute(
            select(ApprovalRequest).where(ApprovalRequest.action_id == staging_action.id)
        ).scalar_one_or_none()
        assert staging_request is None


async def test_forbidden_environment_escalates_the_incident(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _service = _seed_investigation(session, environment=Environment.LOCAL)
        action = await plan_remediation(
            session,
            incident_id,
            llm=_FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT),
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

        assert action.policy_decision == PolicyEffect.FORBID
        assert action.status == ActionStatus.FORBIDDEN.value
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.status == IncidentStatus.ESCALATED


async def test_skips_the_llm_call_when_only_one_candidate_exists(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _service = _seed_investigation(session, with_previous_deploy=False)
        llm = _FakeLLM(chosen_action_type=ActionType.PATCH_CONFIG)
        action = await plan_remediation(
            session, incident_id, llm=llm, clock=FixedClock(datetime.now(UTC))
        )
        session.commit()

        assert llm.calls == []
        assert action.action_type == ActionType.PATCH_CONFIG.value


async def test_raises_when_top_hypothesis_does_not_cite_deployment_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _service = _seed_investigation(session, cite_deployment_evidence=False)
        with pytest.raises(NoRemediationCandidateError, match="does not cite"):
            await plan_remediation(
                session,
                incident_id,
                llm=_FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT),
                clock=FixedClock(datetime.now(UTC)),
            )


async def test_raises_when_incident_is_not_investigating(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        incident_id, _service = _seed_investigation(session)
        incident = session.get(Incident, incident_id)
        assert incident is not None
        incident.status = IncidentStatus.OPEN
        session.commit()

        with pytest.raises(IllegalStateError, match="not INVESTIGATING"):
            await plan_remediation(
                session,
                incident_id,
                llm=_FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT),
                clock=FixedClock(datetime.now(UTC)),
            )


async def test_raises_for_unknown_incident(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session, pytest.raises(NotFoundError):
        await plan_remediation(
            session,
            uuid4(),
            llm=_FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT),
            clock=FixedClock(datetime.now(UTC)),
        )


async def test_creates_the_expected_audit_events(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        incident_id, _service = _seed_investigation(session)
        await plan_remediation(
            session,
            incident_id,
            llm=_FakeLLM(chosen_action_type=ActionType.ROLLBACK_DEPLOYMENT),
            clock=FixedClock(datetime.now(UTC)),
        )
        session.commit()

        events = list(
            session.execute(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.seq)
            )
            .scalars()
            .all()
        )
        event_types = [e.event_type for e in events]
        assert event_types == [
            "remediation_proposed",
            "proposal_requires_approval",
            "approval_requested",
        ]
        assert events[0].actor_type.value == "llm"
        assert events[1].actor_type.value == "system"


def test_select_candidate_returns_the_matching_offered_candidate() -> None:
    rollback = ActionCandidate(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        target_resource="payment-service",
        rationale_hint="x",
        params=RollbackDeploymentParams(
            deployment="payment-service", from_version="v42", to_version="v41"
        ),
    )
    selected = _select_candidate([rollback], ActionType.ROLLBACK_DEPLOYMENT)
    assert selected is rollback


def test_select_candidate_rejects_a_type_not_offered() -> None:
    patch = ActionCandidate(
        action_type=ActionType.PATCH_CONFIG,
        target_resource="payment-service",
        rationale_hint="x",
        params=PatchConfigParams(
            deployment="payment-service",
            changes=[ConfigChange(key="DB_POOL_SIZE", from_value="3", to_value="20")],
        ),
    )
    with pytest.raises(RemediationChoiceError, match="not among the offered"):
        _select_candidate([patch], ActionType.ROLLBACK_DEPLOYMENT)
