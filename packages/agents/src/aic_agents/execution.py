"""The executor: a distinct code path from the investigation graph, holding
its own write-scoped K8s credential (design doc §1.4 ACT row, §1.11,
ADR 0003, T10).

Privilege separation is the property this module exists to prove: every
real mutation goes through `kubectl` invoked with `--server`/
`--certificate-authority`/`--token` flags scoped to the `aic-executor`
ServiceAccount's own minted token (`load_executor_credentials`, sharing
`aic_agents.k8s_auth`'s minting mechanics with the read-only investigator
credential in `aic_agents.tools.k8s`) — never the operator's own admin
kubeconfig, and never the investigator's read-only token. Even a
prompt-injected investigation step has no credential capable of mutating
anything (§1.11's own framing): this module is simply never reachable from
that code path, and the K8s RBAC `Role` bound to `aic-executor`
(`infra/kind/rbac.yaml`, T2) is the real, server-enforced backstop even if
it somehow were.

Two real bugs live-cluster verification caught that no unit test could
have (the same class of finding T7/T9's own notes describe — this is the
one specific to this task): (1) `_kubectl_base_args` passes `--kubeconfig
/dev/null` deliberately, not incidentally — without it, `kubectl` reads
the operator's own ambient `~/.kube/config`, and on a machine where that
config already has a context pointing at the same cluster (exactly what
`kind create cluster` writes), the connection silently authenticates via
THAT context's admin client certificate instead of the scoped `--token`
below, defeating privilege separation entirely while looking, from this
process's point of view, like a correctly-scoped call — this was caught
only by decoding the JWT actually used and proving cross-credential
denial with and without kubeconfig isolation, not by reading the code.
(2) `kubectl rollout undo` resolves the target revision by *listing* the
Deployment's ReplicaSets before it ever patches the Deployment itself —
T2's original `aic-executor` Role granted `get/list/patch` on
`deployments` only, which is not sufficient for the real mechanism ADR
0003 chose; the first live, correctly-isolated attempt failed with a real
403 on `replicasets`, fixed by adding read-only `get/list/watch` on
`replicasets` to that Role.

Handlers per action type (`ROLLBACK_DEPLOYMENT` -> `kubectl rollout undo`,
`PATCH_CONFIG` -> `kubectl set env`) both support `dry_run=True`
(`--dry-run=server`, ADR 0003) so `plan_remediation` (T8) can attach a real
dry-run result to the `ApprovalRequest` row *before* a human decides, and
`dry_run=False` for the real post-approval mutation. `execute_action` is
the orchestration entry point a poller (`apps/aic-executor`) calls: it is
idempotent on `Action.id` — an `ExecutionRecord` already existing for this
action is a signal a previous attempt already ran, and the call becomes a
safe no-op returning that record rather than invoking `kubectl` again, per
this project's "writes never auto-retry silently" convention (a retried
*poller* call must not become a second real rollback, but a failed
execution never triggers automatic application-level retry either — the
human sees `execution_failed`/`ESCALATED`, exactly as escalation-on-fatal-
error is designed to work).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable
from typing import Any
from uuid import UUID

from aic_common.clock import Clock
from aic_common.errors import AICError, IllegalStateError, NotFoundError
from aic_common.ids import new_id
from aic_common.logging import get_logger
from aic_database.models import Action as ActionRow
from aic_database.models import ExecutionRecord as ExecutionRecordRow
from aic_database.models import IncidentEvent
from aic_domain.actions import PatchConfigParams, RollbackDeploymentParams
from aic_domain.enums import (
    ActionStatus,
    ActionType,
    ActorType,
    ExecutionStatus,
    IncidentStatus,
    IncidentTransitionEvent,
)
from aic_domain.state_machine import transition
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aic_agents.approval import load_incident_for_action
from aic_agents.k8s_auth import ServiceAccountCredentials as ServiceAccountCredentials
from aic_agents.k8s_auth import mint_service_account_credentials

logger = get_logger(__name__)

ExecutorK8sCredentials = ServiceAccountCredentials

_KUBECTL_TIMEOUT_SECONDS = 20.0

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class ExecutionError(AICError):
    """A real `kubectl` invocation returned a non-zero exit code."""


def load_executor_credentials(
    *,
    context: str,
    namespace: str,
    service_account: str = "aic-executor",
    token_duration: str = "1h",
    kubectl: str = "kubectl",
) -> ExecutorK8sCredentials:
    """Mint the `aic-executor` ServiceAccount's own credentials from the
    operator's kubeconfig. Every subsequent mutation uses only what's
    returned here — never the investigator's token, never the operator's
    own admin credential."""
    return mint_service_account_credentials(
        context=context,
        namespace=namespace,
        service_account=service_account,
        token_duration=token_duration,
        kubectl=kubectl,
    )


def _default_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=_KUBECTL_TIMEOUT_SECONDS,
        check=False,
    )


def _kubectl_base_args(credentials: ExecutorK8sCredentials, *, kubectl: str) -> list[str]:
    # `--kubeconfig /dev/null` is not optional. Without it, `kubectl` still
    # reads the operator's ambient `~/.kube/config` (or `$KUBECONFIG`) and,
    # if it happens to have a context pointing at the same API server (kind
    # itself writes exactly such a context on `kind create cluster`), the
    # apiserver ends up authenticating the connection via THAT context's
    # client certificate rather than the `--token` passed below — client
    # certs win over a bearer token when both are presented. That would
    # silently authenticate every "executor" call as the operator's own
    # cluster-admin credential and defeat privilege separation entirely
    # (design doc §1.11) while looking, from this process's point of view,
    # exactly like a correctly-scoped call. Caught by live-cluster
    # verification, not by any unit test — nothing here is mockable in a
    # way that would have exposed it.
    return [
        kubectl,
        "--kubeconfig",
        os.devnull,
        "--server",
        credentials.server,
        "--certificate-authority",
        str(credentials.ca_cert_path),
        "--token",
        credentials.token,
        "-n",
        credentials.namespace,
    ]


async def _run(args: list[str], runner: Runner) -> dict[str, Any]:
    result = await asyncio.to_thread(runner, args)
    output = {
        "command": args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.returncode != 0:
        raise ExecutionError(
            f"kubectl command failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return output


async def rollback_deployment(
    credentials: ExecutorK8sCredentials,
    params: RollbackDeploymentParams,
    *,
    dry_run: bool,
    kubectl: str = "kubectl",
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    """`kubectl rollout undo` — naturally idempotent (design doc §1.14:
    rolling back an already-rolled-back Deployment is a no-op), and the
    real primitive ADR 0003 chose over reimplementing revision history."""
    args = [
        *_kubectl_base_args(credentials, kubectl=kubectl),
        "rollout",
        "undo",
        f"deployment/{params.deployment}",
    ]
    if dry_run:
        args.append("--dry-run=server")
    return await _run(args, runner)


async def patch_config(
    credentials: ExecutorK8sCredentials,
    params: PatchConfigParams,
    *,
    dry_run: bool,
    kubectl: str = "kubectl",
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    """`kubectl set env` — reverts every changed key in one call
    (`aic_domain.actions.PatchConfigParams`'s own docstring on why a single
    candidate bundles every changed key)."""
    env_args = [f"{change.key}={change.to_value}" for change in params.changes]
    args = [
        *_kubectl_base_args(credentials, kubectl=kubectl),
        "set",
        "env",
        f"deployment/{params.deployment}",
        *env_args,
    ]
    if dry_run:
        args.append("--dry-run=server")
    return await _run(args, runner)


_HANDLERS: dict[ActionType, Callable[..., Any]] = {
    ActionType.ROLLBACK_DEPLOYMENT: rollback_deployment,
    ActionType.PATCH_CONFIG: patch_config,
}


def _parse_params(
    action_type: ActionType, raw_params: dict[str, Any]
) -> RollbackDeploymentParams | PatchConfigParams:
    if action_type == ActionType.ROLLBACK_DEPLOYMENT:
        return RollbackDeploymentParams.model_validate(raw_params)
    return PatchConfigParams.model_validate(raw_params)


async def dry_run_action(
    action_type: ActionType,
    raw_params: dict[str, Any],
    credentials: ExecutorK8sCredentials,
    *,
    kubectl: str = "kubectl",
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    """Attached to the approval card *before* a human decides (design doc
    §1.4 ACT row, ADR 0003). Called by `aic_agents.remediation
    .plan_remediation` when policy requires approval."""
    handler = _HANDLERS[action_type]
    params = _parse_params(action_type, raw_params)
    result: dict[str, Any] = await handler(
        credentials, params, dry_run=True, kubectl=kubectl, runner=runner
    )
    return result


async def _execute_typed(
    action_type: ActionType,
    raw_params: dict[str, Any],
    credentials: ExecutorK8sCredentials,
    *,
    kubectl: str,
    runner: Runner,
) -> dict[str, Any]:
    handler = _HANDLERS[action_type]
    params = _parse_params(action_type, raw_params)
    result: dict[str, Any] = await handler(
        credentials, params, dry_run=False, kubectl=kubectl, runner=runner
    )
    return result


def _next_seq(session: Session, incident_id: UUID) -> int:
    stmt = select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
        IncidentEvent.incident_id == incident_id
    )
    result: int = session.execute(stmt).scalar_one()
    return result + 1


async def execute_action(
    session: Session,
    action_id: UUID,
    *,
    credentials: ExecutorK8sCredentials,
    clock: Clock,
    kubectl: str = "kubectl",
    runner: Runner = _default_runner,
) -> ExecutionRecordRow:
    """Execute one approved `Action` for real. Idempotent on `action_id`
    (module docstring): a second call for the same action, whatever its
    outcome, is a no-op that returns the first attempt's `ExecutionRecord`
    without invoking `kubectl` again or re-transitioning the incident."""
    action = session.get(ActionRow, action_id)
    if action is None:
        raise NotFoundError(f"no action with id {action_id}")

    existing = session.execute(
        select(ExecutionRecordRow).where(ExecutionRecordRow.action_id == action_id)
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "aic_execution.idempotent_no_op",
            action_id=str(action_id),
            execution_record_id=str(existing.id),
            status=existing.status,
        )
        return existing

    if action.status != ActionStatus.APPROVED.value:
        raise IllegalStateError(
            f"action {action_id} is not approved (status={action.status!r}); cannot execute"
        )

    incident = load_incident_for_action(session, action_id)
    if incident.status != IncidentStatus.REMEDIATING:
        raise IllegalStateError(
            f"incident {incident.id} is not REMEDIATING (status={incident.status.value}); "
            "cannot execute action"
        )

    record = ExecutionRecordRow(
        id=new_id(),
        action_id=action_id,
        started_at=clock.now(),
        status=ExecutionStatus.STARTED.value,
    )
    session.add(record)
    session.flush()

    action_type = ActionType(action.action_type)
    try:
        output = await _execute_typed(
            action_type, action.params, credentials, kubectl=kubectl, runner=runner
        )
    except ExecutionError as exc:
        record.finished_at = clock.now()
        record.status = ExecutionStatus.FAILED.value
        record.output = {"error": str(exc)}
        action.status = ActionStatus.EXECUTION_FAILED.value
        event = IncidentTransitionEvent.FATAL_EXECUTION_ERROR
        incident.status = transition(incident.status, event)
        session.add(
            IncidentEvent(
                incident_id=incident.id,
                seq=_next_seq(session, incident.id),
                event_type="action_execution_failed",
                actor_type=ActorType.SYSTEM,
                payload={
                    "action_id": str(action_id),
                    "execution_record_id": str(record.id),
                    "error": str(exc),
                },
                created_at=clock.now(),
            )
        )
        session.add(
            IncidentEvent(
                incident_id=incident.id,
                seq=_next_seq(session, incident.id),
                event_type=event.value,
                actor_type=ActorType.SYSTEM,
                payload={"action_id": str(action_id)},
                created_at=clock.now(),
            )
        )
        logger.warning(
            "aic_execution.action_failed",
            action_id=str(action_id),
            incident_id=str(incident.id),
            error=str(exc),
        )
        return record

    record.finished_at = clock.now()
    record.status = ExecutionStatus.SUCCEEDED.value
    record.output = output
    action.status = ActionStatus.EXECUTED.value
    event = IncidentTransitionEvent.ACTIONS_EXECUTED
    incident.status = transition(incident.status, event)
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            seq=_next_seq(session, incident.id),
            event_type="action_executed",
            actor_type=ActorType.SYSTEM,
            payload={"action_id": str(action_id), "execution_record_id": str(record.id)},
            created_at=clock.now(),
        )
    )
    session.add(
        IncidentEvent(
            incident_id=incident.id,
            seq=_next_seq(session, incident.id),
            event_type=event.value,
            actor_type=ActorType.SYSTEM,
            payload={"action_id": str(action_id)},
            created_at=clock.now(),
        )
    )
    logger.info(
        "aic_execution.action_executed",
        action_id=str(action_id),
        incident_id=str(incident.id),
        execution_record_id=str(record.id),
    )
    return record
