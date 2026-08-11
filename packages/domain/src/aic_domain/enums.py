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
