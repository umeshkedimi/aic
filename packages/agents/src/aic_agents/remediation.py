"""Remediation planning & policy engine (design doc §1.4 PLAN REMEDIATION /
APPLY POLICY rows, T8).

`plan_remediation` combines both stage-table rows into one function and one
transaction, the same way `aic_agents.triage.triage_incident` combined
TRIAGE's two rows (severity + title) — they always happen together at one
poll and share the incident-level fail-fast check.

Candidate construction is fully deterministic (§1.4: "Candidate type:
deterministic rule"): it looks at the latest RCA's top-ranked `Hypothesis`
(already correctly ordered by T7's self-check), and only proceeds if that
hypothesis cites a `k8s.get_deployment_history` `Evidence` row — the
"top hypothesis cites a deployment-correlation" rule. The service under
investigation is read from that Evidence row's own recorded `query` string
(`"service=<name>"`, per `aic_agents.tools.k8s`'s `render_query`), not from
`Incident.service` — T7's own note flagged `Incident.service` as the
correlation group's canonical key, not necessarily the affected service,
and the same care applies here.

The one LLM call in this stage only ever *selects* between pre-built,
schema-validated `ActionCandidate`s (§1.4: "the LLM only ever selects among
schema-validated, pre-typed options") and writes a rationale; it can never
introduce a new action type or param. If only one candidate exists there is
nothing to choose, so the call is skipped entirely (this project's
recurring "no LLM call where deterministic code suffices" principle).

Policy application (`aic_domain.policy.evaluate_policy`) is deterministic
and decides which `IncidentTransitionEvent` fires:

- `AUTO_APPROVE`  -> `all_actions_auto_approved` -> `REMEDIATING`
- `REQUIRE_APPROVAL` -> `proposal_requires_approval` -> `AWAITING_APPROVAL`,
  plus the actual `ApprovalRequest` row (quorum/required_roles straight off
  the `PolicyRule` already in hand here — no need to re-derive them later).
  T9 added this; see its own module (`aic_agents.approval`) for what
  happens to the request from here (recording decisions, quorum, expiry).
- `FORBID` -> no dedicated state-machine event exists for "policy forbade
  this action", so this uses `HUMAN_TAKEOVER` (closest existing semantic
  fit: automated remediation cannot proceed, a human must decide next
  steps) -> `ESCALATED`. A documented design call, not exercised by the
  scenario's own rule table entries (RollbackDeployment/PatchConfig in
  prod/staging are both covered), but real for any future action type or
  environment that reaches the rule table's default-forbid.
"""

from __future__ import annotations

from uuid import UUID

from aic_common.clock import Clock
from aic_common.errors import IllegalStateError, NotFoundError
from aic_common.ids import new_id
from aic_database.models import RCA as RCARow
from aic_database.models import Action as ActionRow
from aic_database.models import ApprovalRequest as ApprovalRequestRow
from aic_database.models import Deployment as DeploymentRow
from aic_database.models import Evidence as EvidenceRow
from aic_database.models import Hypothesis as HypothesisRow
from aic_database.models import Incident as IncidentRow
from aic_database.models import IncidentEvent
from aic_database.models import PolicyDecision as PolicyDecisionRow
from aic_database.models import RemediationProposal as RemediationProposalRow
from aic_domain.actions import (
    ActionCandidate,
    ConfigChange,
    PatchConfigParams,
    RollbackDeploymentParams,
)
from aic_domain.approval import DEFAULT_APPROVAL_EXPIRY
from aic_domain.enums import (
    ActionStatus,
    ActionType,
    ActorType,
    ApprovalRequestStatus,
    IncidentStatus,
    IncidentTransitionEvent,
    PolicyEffect,
)
from aic_domain.policy import classify_blast_radius, evaluate_policy
from aic_domain.state_machine import transition
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aic_agents.port import LLMPort, ModelTier
from aic_agents.tools.k8s import GET_DEPLOYMENT_HISTORY

_SYSTEM_PROMPT = (
    "You are an SRE choosing a remediation action for an incident whose root "
    "cause has already been established. You are given a closed list of "
    "pre-approved candidate actions with the facts behind each; choose "
    "exactly one by its action type and briefly justify the choice using "
    "only the facts given. You cannot invent a new action or change its "
    "parameters."
)

_FORBID_EFFECT_TO_EVENT = IncidentTransitionEvent.HUMAN_TAKEOVER
_EFFECT_TO_EVENT: dict[PolicyEffect, IncidentTransitionEvent] = {
    PolicyEffect.AUTO_APPROVE: IncidentTransitionEvent.ALL_ACTIONS_AUTO_APPROVED,
    PolicyEffect.REQUIRE_APPROVAL: IncidentTransitionEvent.PROPOSAL_REQUIRES_APPROVAL,
    PolicyEffect.FORBID: _FORBID_EFFECT_TO_EVENT,
}
_EFFECT_TO_ACTION_STATUS: dict[PolicyEffect, ActionStatus] = {
    PolicyEffect.AUTO_APPROVE: ActionStatus.APPROVED,
    PolicyEffect.REQUIRE_APPROVAL: ActionStatus.PENDING_APPROVAL,
    PolicyEffect.FORBID: ActionStatus.FORBIDDEN,
}


class NoRemediationCandidateError(IllegalStateError):
    """The top hypothesis does not cite a deployment-correlation, so no
    deterministic candidate can be built (§1.4's only stated rule)."""


class RemediationChoiceError(IllegalStateError):
    """The LLM chose an action type that was not among the offered
    candidates — never persisted, since that would let free-form model
    output reach an `Action` row."""


class _RemediationChoice(BaseModel):
    chosen_action_type: ActionType
    rationale: str = Field(min_length=1, max_length=2000)


async def plan_remediation(
    session: Session,
    incident_id: UUID,
    *,
    llm: LLMPort,
    clock: Clock,
) -> ActionRow:
    incident = session.get(IncidentRow, incident_id)
    if incident is None:
        raise NotFoundError(f"no incident with id {incident_id}")
    if incident.status != IncidentStatus.INVESTIGATING:
        raise IllegalStateError(
            f"incident {incident_id} is not INVESTIGATING (status={incident.status.value}); "
            "cannot plan remediation"
        )

    rca = _latest_rca(session, incident_id)
    top_hypothesis = _top_hypothesis(session, rca.id)
    candidates = _build_candidates(session, rca.id, top_hypothesis)

    if len(candidates) == 1:
        chosen = candidates[0]
        rationale = f"only one viable candidate: {chosen.rationale_hint}"
        actor_type = ActorType.SYSTEM
    else:
        choice = await llm.complete_structured(
            tier=ModelTier.CHEAP,
            agent_role="remediation-choice",
            system=_SYSTEM_PROMPT,
            user=_render_candidates(candidates),
            response_model=_RemediationChoice,
            incident_id=incident_id,
        )
        chosen = _select_candidate(candidates, choice.chosen_action_type)
        rationale = choice.rationale
        actor_type = ActorType.LLM

    proposal_id = new_id()
    session.add(
        RemediationProposalRow(
            id=proposal_id,
            incident_id=incident_id,
            rca_id=rca.id,
            rationale=rationale,
            created_at=clock.now(),
        )
    )
    # No `relationship()` links these tables (T1's schema is flat FK
    # columns only), so SQLAlchemy's unit-of-work insert ordering does not
    # automatically sequence parent-before-child inserts here — the same
    # class of bug T7 hit between RCA and Hypothesis. Explicit flushes make
    # the insert order deterministic instead of relying on it.
    session.flush()

    action_id = new_id()
    action = ActionRow(
        id=action_id,
        proposal_id=proposal_id,
        action_type=chosen.action_type.value,
        params=chosen.params.model_dump(mode="json"),
        target_resource=chosen.target_resource,
        status=ActionStatus.PROPOSED.value,
        idempotency_key=f"{incident_id}:{rca.id}:{chosen.action_type.value}",
        created_at=clock.now(),
    )
    session.add(action)
    session.flush()

    session.add(
        IncidentEvent(
            incident_id=incident_id,
            seq=_next_seq(session, incident_id),
            event_type="remediation_proposed",
            actor_type=actor_type,
            payload={
                "proposal_id": str(proposal_id),
                "action_id": str(action_id),
                "action_type": chosen.action_type.value,
                "rationale": rationale,
            },
            created_at=clock.now(),
        )
    )

    blast_radius = classify_blast_radius(chosen.action_type)
    rule = evaluate_policy(
        action_type=chosen.action_type,
        environment=incident.environment,
        blast_radius=blast_radius,
    )
    session.add(
        PolicyDecisionRow(
            id=new_id(),
            action_id=action_id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            effect=rule.effect,
            decided_at=clock.now(),
        )
    )
    action.policy_decision = rule.effect
    action.status = _EFFECT_TO_ACTION_STATUS[rule.effect].value

    event = _EFFECT_TO_EVENT[rule.effect]
    incident.status = transition(incident.status, event)
    session.add(
        IncidentEvent(
            incident_id=incident_id,
            seq=_next_seq(session, incident_id),
            event_type=event.value,
            actor_type=ActorType.SYSTEM,
            payload={
                "action_id": str(action_id),
                "rule_id": rule.rule_id,
                "rule_version": rule.version,
                "effect": rule.effect.value,
            },
            created_at=clock.now(),
        )
    )

    if rule.effect == PolicyEffect.REQUIRE_APPROVAL:
        if rule.quorum is None:
            raise IllegalStateError(
                f"policy rule {rule.rule_id!r} (v{rule.version}) requires approval but "
                "declares no quorum"
            )
        approval_request_id = new_id()
        expires_at = clock.now() + DEFAULT_APPROVAL_EXPIRY
        session.add(
            ApprovalRequestRow(
                id=approval_request_id,
                action_id=action_id,
                quorum=rule.quorum,
                required_roles=list(rule.required_roles),
                expires_at=expires_at,
                status=ApprovalRequestStatus.PENDING.value,
                created_at=clock.now(),
            )
        )
        session.add(
            IncidentEvent(
                incident_id=incident_id,
                seq=_next_seq(session, incident_id),
                event_type="approval_requested",
                actor_type=ActorType.SYSTEM,
                payload={
                    "approval_request_id": str(approval_request_id),
                    "action_id": str(action_id),
                    "quorum": rule.quorum,
                    "required_roles": list(rule.required_roles),
                    "expires_at": expires_at.isoformat(),
                },
                created_at=clock.now(),
            )
        )

    return action


def _select_candidate(candidates: list[ActionCandidate], chosen: ActionType) -> ActionCandidate:
    """Validate the LLM's choice against the *actually offered* candidate
    set — schema-valid (`chosen` is a real `ActionType` member) is not the
    same as business-valid (offered in this call). Kept as its own pure
    function so this boundary check is independently unit-testable without
    needing candidate construction to ever organically produce a mismatch
    (today it can't: at most one candidate per action type is ever built,
    so whenever there are 2+ candidates every `ActionType` member is
    covered — this still guards the invariant directly rather than relying
    on that always remaining true)."""
    by_type = {c.action_type: c for c in candidates}
    if chosen not in by_type:
        raise RemediationChoiceError(
            f"LLM chose action type {chosen!r} which was not among the offered "
            f"candidates {sorted(t.value for t in by_type)}"
        )
    return by_type[chosen]


def _latest_rca(session: Session, incident_id: UUID) -> RCARow:
    rca = session.execute(
        select(RCARow)
        .where(RCARow.incident_id == incident_id)
        .order_by(RCARow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if rca is None:
        raise NotFoundError(f"incident {incident_id} has no RCA to plan remediation from")
    return rca


def _top_hypothesis(session: Session, rca_id: UUID) -> HypothesisRow:
    hypothesis = session.execute(
        select(HypothesisRow)
        .where(HypothesisRow.rca_id == rca_id)
        .order_by(HypothesisRow.rank.asc())
        .limit(1)
    ).scalar_one_or_none()
    if hypothesis is None:
        raise NoRemediationCandidateError(f"RCA {rca_id} has no hypotheses")
    return hypothesis


def _build_candidates(
    session: Session, rca_id: UUID, top_hypothesis: HypothesisRow
) -> list[ActionCandidate]:
    deploy_evidence = _find_deployment_evidence(session, top_hypothesis)
    if deploy_evidence is None:
        raise NoRemediationCandidateError(
            f"top hypothesis for RCA {rca_id} does not cite a {GET_DEPLOYMENT_HISTORY} "
            "evidence row — no deployment-correlation rule matched"
        )
    service = _parse_service_from_query(deploy_evidence.query)
    if service is None:
        raise NoRemediationCandidateError(
            f"could not determine target service from evidence {deploy_evidence.id} query "
            f"{deploy_evidence.query!r}"
        )

    deployments = list(
        session.execute(
            select(DeploymentRow)
            .where(DeploymentRow.service == service)
            .order_by(DeploymentRow.deployed_at.desc())
            .limit(2)
        )
        .scalars()
        .all()
    )
    if not deployments:
        raise NoRemediationCandidateError(f"no deployment history found for service {service!r}")

    bad_deploy = deployments[0]
    previous_deploy = deployments[1] if len(deployments) > 1 else None

    candidates: list[ActionCandidate] = []
    if previous_deploy is not None:
        candidates.append(
            ActionCandidate(
                action_type=ActionType.ROLLBACK_DEPLOYMENT,
                target_resource=service,
                rationale_hint=(
                    f"deploy {bad_deploy.version} at {bad_deploy.deployed_at.isoformat()} "
                    f"immediately precedes symptom onset; rolling back to "
                    f"{previous_deploy.version} restores the last known-good deployment"
                ),
                params=RollbackDeploymentParams(
                    deployment=service,
                    from_version=bad_deploy.version,
                    to_version=previous_deploy.version,
                ),
            )
        )

    changes = [
        ConfigChange(key=key, from_value=str(diff["to"]), to_value=str(diff["from"]))
        for key, diff in bad_deploy.config_diff.items()
        if isinstance(diff, dict) and "from" in diff and "to" in diff
    ]
    if changes:
        # One candidate bundling every changed key, not one candidate per
        # key (see `PatchConfigParams`'s docstring) — a single `PatchConfig`
        # action_type must stay a true binary alternative to
        # `RollbackDeployment`, not one of several same-typed options an
        # `action_type`-only choice can't disambiguate between.
        summary = ", ".join(f"{c.key} {c.from_value!r}->{c.to_value!r}" for c in changes)
        candidates.append(
            ActionCandidate(
                action_type=ActionType.PATCH_CONFIG,
                target_resource=service,
                rationale_hint=(
                    f"deploy {bad_deploy.version} changed [{summary}]; patching them back "
                    "does not require a full rollback"
                ),
                params=PatchConfigParams(deployment=service, changes=changes),
            )
        )

    if not candidates:
        raise NoRemediationCandidateError(
            f"deploy {bad_deploy.id} for service {service!r} has neither a prior version to "
            "roll back to nor a config_diff to patch"
        )
    return candidates


def _find_deployment_evidence(
    session: Session, top_hypothesis: HypothesisRow
) -> EvidenceRow | None:
    evidence_ids = [UUID(e) for e in top_hypothesis.evidence_ids]
    if not evidence_ids:
        return None
    return session.execute(
        select(EvidenceRow)
        .where(EvidenceRow.id.in_(evidence_ids))
        .where(EvidenceRow.tool == GET_DEPLOYMENT_HISTORY)
        .limit(1)
    ).scalar_one_or_none()


def _parse_service_from_query(query: str | None) -> str | None:
    if query is None or not query.startswith("service="):
        return None
    service = query.removeprefix("service=")
    return service or None


def _render_candidates(candidates: list[ActionCandidate]) -> str:
    lines = ["Candidate remediation actions:"]
    for candidate in candidates:
        lines.append(
            f"- {candidate.action_type.value} on {candidate.target_resource}: "
            f"{candidate.rationale_hint} (params: {candidate.params.model_dump(mode='json')})"
        )
    return "\n".join(lines)


def _next_seq(session: Session, incident_id: UUID) -> int:
    stmt = select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
        IncidentEvent.incident_id == incident_id
    )
    result: int = session.execute(stmt).scalar_one()
    return result + 1
