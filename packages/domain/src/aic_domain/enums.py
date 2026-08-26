"""Closed value sets used across the domain model.

Only fields whose full value set is pinned down by the design doc get an
enum here. Fields whose lifecycle a later task will define precisely
(``Action.status``, `ApprovalRequest.status`, ``RCA.status``, ...) stay
plain ``str`` in :mod:`aic_domain.models` rather than guessing at a set now.
"""

from __future__ import annotations

from enum import StrEnum


class IncidentStatus(StrEnum):
    """States of the incident state machine (design doc §6)."""

    OPEN = "open"
    TRIAGING = "triaging"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    REMEDIATING = "remediating"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ESCALATED = "escalated"
    FAILED = "failed"


class IncidentTransitionEvent(StrEnum):
    """Events that drive `IncidentStatus` transitions (design doc §6)."""

    WORKFLOW_STARTED = "workflow_started"
    TRIAGE_COMPLETED = "triage_completed"
    PROPOSAL_REQUIRES_APPROVAL = "proposal_requires_approval"
    ALL_ACTIONS_AUTO_APPROVED = "all_actions_auto_approved"
    BUDGET_EXHAUSTED = "budget_exhausted"
    HUMAN_TAKEOVER = "human_takeover"
    QUORUM_MET = "quorum_met"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ACTIONS_EXECUTED = "actions_executed"
    FATAL_EXECUTION_ERROR = "fatal_execution_error"
    SOAK_PASSED = "soak_passed"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_FAILED_NO_ROLLBACK = "verification_failed_no_rollback"
    HUMAN_RESOLVED = "human_resolved"
    POST_REVIEW = "post_review"
    ESCALATE = "escalate"


class Severity(StrEnum):
    """Incident severity, assigned deterministically by the triage rule table."""

    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"


class ActorType(StrEnum):
    """Who/what caused an `IncidentEvent` — the audit spine's actor classification."""

    SYSTEM = "system"
    LLM = "llm"
    HUMAN = "human"


class EvidenceStatus(StrEnum):
    """Outcome of a single investigation tool call (design doc §1.9)."""

    OK = "ok"
    ERROR = "error"


class PolicyEffect(StrEnum):
    """Possible outcomes of a policy rule evaluation (design doc §1.10)."""

    AUTO_APPROVE = "auto_approve"
    REQUIRE_APPROVAL = "require_approval"
    FORBID = "forbid"


class ApprovalDecisionType(StrEnum):
    """A human decider's vote on an `ApprovalRequest`."""

    APPROVE = "approve"
    REJECT = "reject"


class LLMCallStatus(StrEnum):
    """Outcome of a single LLM completion attempt (design doc ADR 0004,
    §1.14 "Malformed/hallucinated LLM output": retry-with-feedback, max 2
    attempts). Mirrors `EvidenceStatus` (ok/error) plus the one outcome
    unique to structured-output calls: the response came back but failed
    schema validation."""

    OK = "ok"
    VALIDATION_FAILED = "validation_failed"
    ERROR = "error"


class ActionType(StrEnum):
    """Closed remediation action catalog (design doc §1.4 PLAN REMEDIATION
    row, §1.10). Values match the design doc's own literal action-type
    names, since they appear verbatim in `Action.action_type` and in the
    policy rule table's worked example."""

    ROLLBACK_DEPLOYMENT = "RollbackDeployment"
    PATCH_CONFIG = "PatchConfig"


class BlastRadius(StrEnum):
    """Policy predicate input (design doc §1.10:
    `action_type x environment x blast-radius predicate -> effect`). The
    action catalog only ever targets one Deployment at a time today, so
    `SINGLE_SERVICE` is the only value any candidate actually produces;
    `MULTI_SERVICE` exists so the rule table's shape is already right for a
    future action type that fans out across services, not because anything
    classifies into it yet."""

    SINGLE_SERVICE = "single_service"
    MULTI_SERVICE = "multi_service"


class ActionStatus(StrEnum):
    """Action lifecycle values used in application code. NOT mapped as a
    Postgres enum column — `Action.status` stays a plain string (T1's own
    note: "fields whose lifecycle a later task will define precisely ...
    stay plain str"), because this lifecycle keeps growing across T8-T10
    (T9 adds approval outcomes, T10 adds execution outcomes); a Python-level
    closed set without a DB enum avoids an `ALTER TYPE ... ADD VALUE`
    migration every time a downstream task extends it."""

    PROPOSED = "proposed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    FORBIDDEN = "forbidden"


class ApprovalRequestStatus(StrEnum):
    """`ApprovalRequest` lifecycle values (design doc §1.10, T9). Like
    `ActionStatus`, NOT mapped as a Postgres enum column —
    `ApprovalRequest.status` stays a plain string, per T1's "fields whose
    lifecycle a later task will define precisely stay plain str" note."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
