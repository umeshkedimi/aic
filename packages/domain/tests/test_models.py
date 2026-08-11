from datetime import UTC, datetime
from uuid import uuid4

import pytest
from aic_common.config import Environment
from aic_domain.enums import (
    ActorType,
    ApprovalDecisionType,
    EvidenceStatus,
    IncidentStatus,
    PolicyEffect,
)
from aic_domain.models import (
    RCA,
    Action,
    ApprovalDecision,
    ApprovalRequest,
    Deployment,
    Evidence,
    ExecutionRecord,
    Hypothesis,
    Incident,
    IncidentEvent,
    IncidentSignal,
    PolicyDecision,
    Postmortem,
    RemediationProposal,
    ServiceDependency,
    VerificationRecord,
)
from pydantic import ValidationError

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def test_incident_round_trips_and_is_mutable() -> None:
    incident = Incident(
        id=uuid4(),
        fingerprint="payment-service:prod:pool-exhaustion",
        service="payment-service",
        environment=Environment.PROD,
        created_at=NOW,
    )
    assert incident.status == IncidentStatus.OPEN
    incident.status = IncidentStatus.TRIAGING
    assert incident.status == IncidentStatus.TRIAGING
    assert Incident.model_validate(incident.model_dump()) == incident


def test_incident_event_is_frozen() -> None:
    event = IncidentEvent(
        id=uuid4(),
        incident_id=uuid4(),
        seq=1,
        event_type="workflow_started",
        actor_type=ActorType.SYSTEM,
        created_at=NOW,
    )
    with pytest.raises(ValidationError):
        event.seq = 2


def test_evidence_status_and_optional_fields() -> None:
    evidence = Evidence(
        id=uuid4(),
        incident_id=uuid4(),
        source="prometheus",
        tool="range_query",
        query="histogram_quantile(0.99, ...)",
        result_digest="p99 latency rose from 40ms to 1200ms over 5m",
        latency_ms=180,
        collected_at=NOW,
        status=EvidenceStatus.OK,
    )
    assert evidence.status == EvidenceStatus.OK
    with pytest.raises(ValidationError):
        Evidence(
            id=uuid4(),
            incident_id=uuid4(),
            source="prometheus",
            tool="range_query",
            collected_at=NOW,
            status=EvidenceStatus.OK,
            latency_ms=-1,
        )


def test_hypothesis_confidence_bounds_and_demotion() -> None:
    with pytest.raises(ValidationError):
        Hypothesis(id=uuid4(), rca_id=uuid4(), rank=1, statement="x", confidence=1.5)

    hyp = Hypothesis(id=uuid4(), rca_id=uuid4(), rank=1, statement="x", confidence=0.9)
    hyp.demoted_reason = "deploy time cited postdates symptom onset"
    hyp.rank = 2
    assert hyp.demoted_reason is not None


def test_utc_datetime_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        RCA(
            id=uuid4(),
            incident_id=uuid4(),
            agent_version="0.1.0",
            status="draft",
            created_at=datetime(2026, 8, 11, 12, 0),  # naive
        )


def test_approval_decision_is_immutable() -> None:
    decision = ApprovalDecision(
        id=uuid4(),
        approval_request_id=uuid4(),
        decider_id="sre-oncall",
        decision=ApprovalDecisionType.APPROVE,
        decided_at=NOW,
    )
    with pytest.raises(ValidationError):
        decision.decision = ApprovalDecisionType.REJECT


def test_approval_request_status_is_mutable() -> None:
    request = ApprovalRequest(
        id=uuid4(),
        action_id=uuid4(),
        quorum=1,
        required_roles=["sre"],
        expires_at=NOW,
        created_at=NOW,
    )
    assert request.status == "pending"
    request.status = "approved"
    assert request.status == "approved"


def test_action_and_policy_decision() -> None:
    action = Action(
        id=uuid4(),
        proposal_id=uuid4(),
        action_type="RollbackDeployment",
        target_resource="deployment/payment-service",
        status="proposed",
        idempotency_key="incident-42-rollback-1",
        created_at=NOW,
    )
    policy = PolicyDecision(
        id=uuid4(),
        action_id=action.id,
        rule_id="rollback-requires-approval-in-prod",
        rule_version=1,
        effect=PolicyEffect.REQUIRE_APPROVAL,
        decided_at=NOW,
    )
    assert policy.action_id == action.id
    assert policy.effect == PolicyEffect.REQUIRE_APPROVAL


def test_execution_and_verification_records() -> None:
    execution = ExecutionRecord(id=uuid4(), action_id=uuid4(), started_at=NOW, status="running")
    execution.status = "succeeded"
    verification = VerificationRecord(
        id=uuid4(),
        execution_id=execution.id,
        metric_snapshots={"before": {"p99_ms": 1200}, "after": {"p99_ms": 45}},
        passed=True,
        checked_at=NOW,
    )
    assert verification.passed is True


def test_service_dependency_and_deployment_are_facts() -> None:
    dependency = ServiceDependency(service="checkout-service", depends_on="payment-service")
    with pytest.raises(ValidationError):
        dependency.depends_on = "postgres"

    deployment = Deployment(
        id=uuid4(),
        service="payment-service",
        version="v42",
        image_tag="payment-service:v42",
        config_diff={"DB_POOL_SIZE": {"old": 20, "new": 3}},
        deployed_at=NOW,
        deployed_by="deploy-script",
    )
    assert deployment.config_diff["DB_POOL_SIZE"]["new"] == 3


def test_incident_signal_and_remediation_proposal_and_postmortem_smoke() -> None:
    signal = IncidentSignal(
        id=uuid4(),
        incident_id=uuid4(),
        alert_fingerprint="abc123",
        alertname="HighLatencyPaymentService",
        service="payment-service",
        labels={"severity": "critical"},
        starts_at=NOW,
    )
    proposal = RemediationProposal(
        id=uuid4(),
        incident_id=signal.incident_id,
        rca_id=uuid4(),
        rationale="Symptom onset immediately follows v42 deploy; rollback restores pool=20.",
        created_at=NOW,
    )
    postmortem = Postmortem(
        id=uuid4(),
        incident_id=signal.incident_id,
        content="## Timeline\n...",
        created_at=NOW,
    )
    assert proposal.incident_id == signal.incident_id == postmortem.incident_id
