"""Verification & resolution (design doc §1.4 VERIFY/RESOLVE rows, §1.12,
T11): after a 90-second soak window post-action, re-run the same
Prometheus/Loki queries `gather` (T7) used, compare against the thresholds
that fired the original Alertmanager rules (T3), and make a purely
deterministic pass/fail call — "did the number cross back over the line",
no LLM involvement.

**Query reuse.** `p99_query`/`pool_query`/`log_query`
(`aic_agents.graphs.investigation`) are imported directly, not
re-implemented, so the incident-window and post-soak PromQL/LogQL text are
provably identical. Two of the three real alert thresholds
(`HighLatencyPaymentService`, `DBPoolExhaustionPaymentService`'s
`db_pool_connections_in_use` half) map onto queries `gather` already runs.
Two queries below have no `gather` counterpart, because `gather` never
needed them as *investigation* evidence, and are added here to make the
verifier actually check what the alerts checked: `db_pool_max_size` (the
other half of the pool-exhaustion comparison — `gather` only ever fetched
utilization, never the cap) and the `HighErrorRatePaymentService` 5xx-ratio
query (no line of inquiry in `plan()` ever gathered it — logs were the
qualitative substitute during investigation). Both mirror their alert's
PromQL from `infra/kind/observability/prometheus.yaml` exactly, alert
operator stripped the same way `p99_query` already omits its own `> 1`.

**Target service.** Verification checks the *actual* `Action.target_resource`
the executor mutated (e.g. `payment-service`), not a re-derivation from
`Incident.service` (the correlation group's canonical key) or
`IncidentSignal`. This is more direct than T7's own signal-derivation
dance and sidesteps that whole class of ambiguity: whatever service the
executor's `kubectl` command actually named is unambiguously the one
whose metrics need to come back under threshold.

**Loki is corroborative, not gating.** None of the three real alert rules
are Loki-based, so there is no numeric LogQL threshold to compare
against; the post-soak log query is re-run and its result count is stored
in `metric_snapshots` for a human/postmortem to see, but `passed` is
decided purely by the three Prometheus checks.

**Two short transactions, not one held open across the soak wait.** The
soak sleep plus the real Prometheus/Loki HTTP calls can take well over 90
seconds; holding a Postgres session/connection idle-in-transaction for
that whole span (as a single `session_scope` block would) pins a pooled
connection for no reason and risks an idle-in-transaction timeout on a
less permissive Postgres config. `verify_incident` therefore takes a
`session_factory`, not a `session` (unlike T9/T10's `record_decision`/
`execute_action`, whose external calls are all short and bounded by their
own explicit timeouts): one short transaction to read what's needed, the
soak/query work with no DB session open, then a second short transaction
to persist the result and drive the state transition.

**Idempotency.** `VerificationRecord.execution_id` is unique (migration
`596606b61f0e`) — the real backstop, mirroring T9's DB-level immutability
trigger. `verify_incident` also checks for an existing record up front and
returns it without repeating the soak/queries, the same "a retried poller
call is a safe no-op" convention as `execute_action` (T10).

**The one-retry bound.** `aic_domain.state_machine.transition()`'s own
docstring says counting retries is this module's job, since `transition()`
is stateless. `_count_prior_verification_failures` counts existing
`VerificationRecord(passed=False)` rows already attached (via
`ExecutionRecord -> Action -> RemediationProposal`) to this incident: zero
prior failures -> `VERIFICATION_FAILED` (loop back to `investigating`,
this is the one allowed retry); one or more -> `VERIFICATION_FAILED_NO_ROLLBACK`
(-> `escalated`, per §6 — despite the event's name, this module does not
itself roll anything back; the name is the diagram's own, describing "no
more automated remediation attempts left," not a literal rollback action).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from aic_common.clock import Clock
from aic_common.errors import IllegalStateError, NotFoundError
from aic_common.ids import new_id
from aic_common.logging import get_logger
from aic_database.models import Action as ActionRow
from aic_database.models import Evidence as EvidenceRow
from aic_database.models import ExecutionRecord as ExecutionRecordRow
from aic_database.models import Incident as IncidentRow
from aic_database.models import IncidentEvent
from aic_database.models import RemediationProposal as RemediationProposalRow
from aic_database.models import VerificationRecord as VerificationRecordRow
from aic_domain.enums import (
    ActorType,
    EvidenceStatus,
    IncidentStatus,
    IncidentTransitionEvent,
)
from aic_domain.state_machine import transition
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aic_agents.graphs.investigation import log_query, p99_query, pool_query

logger = get_logger(__name__)

_SOAK_SECONDS = 90.0
_LATENCY_THRESHOLD_SECONDS = 1.0
_ERROR_RATE_THRESHOLD = 0.05


def _error_rate_query(service: str) -> str:
    """Mirrors `HighErrorRatePaymentService`'s PromQL exactly (operator
    stripped, same convention as `p99_query`). `gather` never runs this —
    see module docstring."""
    return (
        f'sum(rate(http_requests_total{{app="{service}",status=~"5.."}}[1m])) '
        f'/ sum(rate(http_requests_total{{app="{service}"}}[1m]))'
    )


def _pool_max_query(service: str) -> str:
    """The other half of `DBPoolExhaustionPaymentService`'s comparison.
    `gather` never runs this — see module docstring."""
    return f'db_pool_max_size{{app="{service}"}}'


@dataclass(slots=True)
class CheckResult:
    name: str
    query: str
    value: float | None
    threshold: float | None
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "value": self.value,
            "threshold": self.threshold,
            "passed": self.passed,
        }


async def _instant_scalar(client: httpx.AsyncClient, query: str, *, at: datetime) -> float | None:
    response = await client.get("/api/v1/query", params={"query": query, "time": at.timestamp()})
    response.raise_for_status()
    result = response.json().get("data", {}).get("result", [])
    if not result:
        return None
    return float(result[0]["value"][1])


async def _check_latency(client: httpx.AsyncClient, service: str, *, at: datetime) -> CheckResult:
    query = p99_query(service)
    value = await _instant_scalar(client, query, at=at)
    # No data (empty vector) is what a healthy, quiet service also looks
    # like — Prometheus's own alert wouldn't fire on it either, so absence
    # of a value is treated as "not breaching", never as a failure.
    passed = value is None or value <= _LATENCY_THRESHOLD_SECONDS
    return CheckResult("latency", query, value, _LATENCY_THRESHOLD_SECONDS, passed)


async def _check_error_rate(
    client: httpx.AsyncClient, service: str, *, at: datetime
) -> CheckResult:
    query = _error_rate_query(service)
    value = await _instant_scalar(client, query, at=at)
    passed = value is None or value <= _ERROR_RATE_THRESHOLD
    return CheckResult("error_rate", query, value, _ERROR_RATE_THRESHOLD, passed)


async def _check_pool(client: httpx.AsyncClient, service: str, *, at: datetime) -> CheckResult:
    in_use_query = pool_query(service)
    in_use = await _instant_scalar(client, in_use_query, at=at)
    max_size = await _instant_scalar(client, _pool_max_query(service), at=at)
    # Missing either series (pool metrics not scraped, service down, ...)
    # is inconclusive, not evidence of exhaustion — treated as passing,
    # same "absence isn't breach" reasoning as the other two checks.
    passed = in_use is None or max_size is None or in_use < max_size
    return CheckResult("pool_exhaustion", in_use_query, in_use, max_size, passed)


async def _recent_log_count(
    client: httpx.AsyncClient, service: str, *, window_start: datetime, window_end: datetime
) -> int:
    """Corroborative only (module docstring) — re-runs the exact same
    LogQL `gather` used, over the soak window, so a human/postmortem can
    see whether the fault's own log signature (e.g. `pool_exhausted`)
    recurred, without this count ever gating `passed`."""
    response = await client.get(
        "/loki/api/v1/query_range",
        params={
            "query": log_query(service),
            "start": int(window_start.timestamp() * 1_000_000_000),
            "end": int(window_end.timestamp() * 1_000_000_000),
            "limit": 1000,
        },
    )
    response.raise_for_status()
    streams = response.json().get("data", {}).get("result", [])
    return sum(len(stream.get("values", [])) for stream in streams)


def _next_seq(session: Session, incident_id: UUID) -> int:
    stmt = select(func.coalesce(func.max(IncidentEvent.seq), 0)).where(
        IncidentEvent.incident_id == incident_id
    )
    result: int = session.execute(stmt).scalar_one()
    return result + 1


def _count_prior_verification_failures(session: Session, incident_id: UUID) -> int:
    stmt = (
        select(func.count(VerificationRecordRow.id))
        .join(ExecutionRecordRow, ExecutionRecordRow.id == VerificationRecordRow.execution_id)
        .join(ActionRow, ActionRow.id == ExecutionRecordRow.action_id)
        .join(RemediationProposalRow, RemediationProposalRow.id == ActionRow.proposal_id)
        .where(RemediationProposalRow.incident_id == incident_id)
        .where(VerificationRecordRow.passed.is_(False))
    )
    result: int = session.execute(stmt).scalar_one()
    return result


@dataclass(slots=True)
class _PreparedVerification:
    action_id: UUID
    execution_id: UUID
    target_service: str
    existing: VerificationRecordRow | None


def _prepare(session_factory: sessionmaker[Session], incident_id: UUID) -> _PreparedVerification:
    with session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        if incident is None:
            raise NotFoundError(f"no incident with id {incident_id}")

        execution = (
            session.execute(
                select(ExecutionRecordRow)
                .join(ActionRow, ActionRow.id == ExecutionRecordRow.action_id)
                .join(RemediationProposalRow, RemediationProposalRow.id == ActionRow.proposal_id)
                .where(RemediationProposalRow.incident_id == incident_id)
                .order_by(ExecutionRecordRow.started_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if execution is None:
            raise NotFoundError(f"incident {incident_id} has no execution record to verify")

        existing = session.execute(
            select(VerificationRecordRow).where(VerificationRecordRow.execution_id == execution.id)
        ).scalar_one_or_none()
        if existing is not None:
            return _PreparedVerification(
                action_id=execution.action_id,
                execution_id=execution.id,
                target_service="",
                existing=existing,
            )

        if incident.status != IncidentStatus.VERIFYING:
            raise IllegalStateError(
                f"incident {incident_id} is not VERIFYING (status={incident.status.value}); "
                "cannot verify"
            )

        action = session.get(ActionRow, execution.action_id)
        assert action is not None
        return _PreparedVerification(
            action_id=action.id,
            execution_id=execution.id,
            target_service=action.target_resource,
            existing=None,
        )


def _persist(
    session_factory: sessionmaker[Session],
    *,
    incident_id: UUID,
    execution_id: UUID,
    checks: list[CheckResult],
    log_count: int,
    clock: Clock,
) -> VerificationRecordRow:
    passed = all(check.passed for check in checks)
    now = clock.now()

    with session_factory() as session:
        incident = session.get(IncidentRow, incident_id)
        assert incident is not None
        if incident.status != IncidentStatus.VERIFYING:
            raise IllegalStateError(
                f"incident {incident_id} is not VERIFYING (status={incident.status.value}); "
                "cannot persist verification result"
            )

        # Counted *before* the new record is added: `_count_prior_verification
        # _failures` would otherwise see this call's own not-yet-committed
        # row once flushed (same session, same transaction) and off-by-one
        # itself into escalating on the very first failure.
        prior_failures = _count_prior_verification_failures(session, incident_id)

        record = VerificationRecordRow(
            id=new_id(),
            execution_id=execution_id,
            metric_snapshots={
                **{check.name: check.as_dict() for check in checks},
                "recent_log_count": log_count,
            },
            passed=passed,
            checked_at=now,
        )
        session.add(record)
        session.flush()

        if passed:
            incident.status = transition(incident.status, IncidentTransitionEvent.SOAK_PASSED)
            incident.resolved_at = now
            descriptive_event = "verification_passed"
            transition_event = IncidentTransitionEvent.SOAK_PASSED
        else:
            # New Evidence per design doc §1.12/§6: "the failed
            # verification becomes new Evidence" for the next
            # investigation pass to see.
            session.add(
                EvidenceRow(
                    id=new_id(),
                    incident_id=incident_id,
                    source="verifier",
                    tool="verification.soak_check",
                    query=None,
                    result_digest=str(record.metric_snapshots),
                    latency_ms=None,
                    collected_at=now,
                    status=EvidenceStatus.OK,
                )
            )
            transition_event = (
                IncidentTransitionEvent.VERIFICATION_FAILED
                if prior_failures == 0
                else IncidentTransitionEvent.VERIFICATION_FAILED_NO_ROLLBACK
            )
            incident.status = transition(incident.status, transition_event)
            descriptive_event = "verification_failed"

        session.add(
            IncidentEvent(
                incident_id=incident_id,
                seq=_next_seq(session, incident_id),
                event_type=descriptive_event,
                actor_type=ActorType.SYSTEM,
                payload={
                    "execution_id": str(execution_id),
                    "verification_record_id": str(record.id),
                },
                created_at=now,
            )
        )
        session.add(
            IncidentEvent(
                incident_id=incident_id,
                seq=_next_seq(session, incident_id),
                event_type=transition_event.value,
                actor_type=ActorType.SYSTEM,
                payload={"execution_id": str(execution_id)},
                created_at=now,
            )
        )
        session.commit()
        logger.info(
            "aic_verification.verified",
            incident_id=str(incident_id),
            execution_id=str(execution_id),
            passed=passed,
            new_status=incident.status.value,
        )
        return record


async def verify_incident(
    *,
    session_factory: sessionmaker[Session],
    incident_id: UUID,
    prometheus_client: httpx.AsyncClient,
    loki_client: httpx.AsyncClient,
    clock: Clock,
    soak_seconds: float = _SOAK_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> VerificationRecordRow:
    """Verify the incident's most recent execution (design doc §1.12).
    Idempotent on `execution_id` (module docstring): a retried call for an
    execution that already has a `VerificationRecord` is a no-op that
    returns the existing record without sleeping or re-querying."""
    prepared = await asyncio.to_thread(_prepare, session_factory, incident_id)
    if prepared.existing is not None:
        logger.info(
            "aic_verification.idempotent_no_op",
            incident_id=str(incident_id),
            execution_id=str(prepared.execution_id),
        )
        return prepared.existing

    await sleep(soak_seconds)

    soak_start = clock.now() - timedelta(seconds=soak_seconds)
    now = clock.now()
    latency, error_rate, pool, log_count = await asyncio.gather(
        _check_latency(prometheus_client, prepared.target_service, at=now),
        _check_error_rate(prometheus_client, prepared.target_service, at=now),
        _check_pool(prometheus_client, prepared.target_service, at=now),
        _recent_log_count(
            loki_client, prepared.target_service, window_start=soak_start, window_end=now
        ),
    )

    return await asyncio.to_thread(
        _persist,
        session_factory,
        incident_id=incident_id,
        execution_id=prepared.execution_id,
        checks=[latency, error_rate, pool],
        log_count=log_count,
        clock=clock,
    )
