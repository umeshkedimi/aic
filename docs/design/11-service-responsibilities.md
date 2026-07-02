# 11. Service Responsibilities

For each service: what it owns, what it explicitly must never do, how it scales, how it fails,
and what it is trusted with. The "never does" lists are as architecturally binding as the
responsibilities — they are what keeps the privilege model true over time.

## 11.1 `aic-api` — control plane

**Owns**
- All human- and machine-facing REST endpoints (`/api/v1/*`), OpenAPI contract
- Authentication (OIDC exchange, JWT issue/verify, API keys) and RBAC enforcement
- Approval intake: web + Slack interaction callbacks → validated → Temporal signal
- Read models: incident timelines, evidence, RCA, audit queries, exports
- Admin surface: policies, integration configs, alert sources, role assignments
- Knowledge ingestion API (validation + handoff to embedding pipeline)

**Never does:** call LLMs; hold infrastructure credentials; execute actions; mutate incident
state directly (state changes flow through workflow signals or domain services that append
events); run long-lived work in request handlers.

| Aspect | Design |
|---|---|
| State | Stateless; horizontal scale behind the ingress |
| Scaling signal | CPU + p99 latency; HPA 2→10 replicas |
| DB role | `aic_api`: read broadly; write `iam.*`, `policy.*`, `incident.approval_*`, knowledge metadata; **no write** on event/evidence/action tables |
| Degradation | Temporal down → approvals queue as pending rows, signal on recovery; Redis down → rate limiting fails open with alert (availability over strictness for reads) |
| SLO | 99.9% availability; p99 < 300 ms reads |

## 11.2 `aic-ingest` — ingestion pipeline

**Owns**
- Per-source webhook endpoints with per-source auth (HMAC/shared secret + replay window)
- Normalization to canonical `AlertEvent` (raw payload preserved verbatim)
- Persist-before-ack durability; reconciliation sweep for orphaned alerts
- Dedup (Redis fingerprint window) and correlation (open-incident matching)
- Publishing `IncidentTriggered` to the bus; stream consumer that starts workflows idempotently

**Never does:** reason about alert content (no LLM); serve human traffic; talk to integrations;
decide severity (that's triage — ingest only maps source-native severity as a hint).

| Aspect | Design |
|---|---|
| State | Stateless; consumer group membership is the only coordination |
| Scaling signal | Request rate + stream lag; sized for 500 alerts/s burst (NFR-3.1) |
| DB role | `aic_ingest`: write `incident.alert_event`, `incident.incident` (create/correlate), append `incident_event`; nothing else |
| Degradation | Redis down → skip dedup, keep accepting (duplicate incidents beat lost alerts); bus down → alerts persist, reconciler republishes; Postgres down → 503 (sources retry; Alertmanager/Datadog do) |
| SLO | 99.9%; webhook ack p99 < 500 ms |

## 11.3 `aic-worker` — intelligence plane

**Owns**
- `IncidentWorkflow` definition (deterministic sequencing, timers, approval signals)
- Activities: triage, investigate, RCA, propose, document — each idempotent, budgeted,
  heartbeating
- LangGraph execution, read-only tool bindings, RAG retrieval/context assembly
- First-pass policy evaluation (labeling proposals with decisions)
- LLM access via `LLMPort`; per-incident budget enforcement; LLM telemetry emission

**Never does:** execute write actions against any external system; hold write credentials
(package-level: write adapters not installed); approve anything; serve HTTP (only Temporal task
polling + health/metrics endpoints).

| Aspect | Design |
|---|---|
| State | None in-process; all progress in Temporal, all results in Postgres |
| Scaling signal | Temporal task queue depth (`investigation` queue); concurrency governed by LLM rate limits, not CPU |
| DB role | `aic_worker`: append events/evidence/rca/proposals; read knowledge + policy; **no write** on approvals, executions, iam, policy |
| Degradation | LLM provider down → activity retries with backoff → after budget, partial-result escalation to humans; single integration down → circuit breaker opens, investigation proceeds with recorded gap |
| SLO | Time-to-first-hypothesis p95 < 3 min while queue depth within capacity plan |

## 11.4 `aic-executor` — execution plane

**Owns**
- The only write path to external systems, on a dedicated Temporal task queue
- Independent gate: re-read approval + policy from Postgres, re-evaluate policy, verify quorum —
  refuses on any mismatch and raises `SecurityEvent`
- Action handlers (one per catalog type): dry-run, execute, typed results
- Per-target remediation locks; verification probes over soak windows; rollback coordination

**Never does:** call LLMs (nothing in this service is probabilistic — by design there is no
prompt-injection surface in the execution plane); accept work from anything but its task queue;
trust its caller ("the workflow said it's approved" is not evidence — the database record is).

| Aspect | Design |
|---|---|
| State | Stateless; locks and results in Postgres |
| Scaling signal | Execution queue depth; typically 2 replicas (executions are rare and short relative to investigations) |
| DB role | `aic_executor`: read approvals/policies/actions; write `execution_record`, `verification_record`, action status; append events |
| Degradation | Target system unreachable → typed retryable failure, activity backoff, then human escalation with exact error; **never** best-effort partial application of an action plan |
| SLO | Approval → execution start p95 < 10 s |
| Isolation | Separate K8s ServiceAccount; NetworkPolicy allows egress only to declared targets; the only pod with write RBAC on tenant clusters |

## 11.5 Write-ownership matrix

Every table has exactly one writer (§9.4). The matrix is the reviewable artifact:

| Table (schema.table) | api | ingest | worker | executor |
|---|---|---|---|---|
| incident.alert_event | | **W** | | |
| incident.incident | correlate-link | **W** (create) | status via events | |
| incident.incident_event | A | A | A | A |
| incident.evidence | | | **W** | |
| incident.rca / hypothesis | | | **W** | |
| incident.remediation_proposal / action | | | **W** (propose) | status updates |
| incident.approval_request / decision | **W** | | | R |
| incident.execution_record / verification_record | | | | **W** |
| knowledge.document / chunk | **W** (meta) | | W (embeddings, incident records) | |
| policy.policy_rule | **W** | | R | R |
| iam.* | **W** | | | |
| llmops.llm_call | | | **W** | |

`A` = append-only. Shared appends to `incident_event` are safe: the table is insert-only and
every row carries its writer's identity.

## 11.6 Trust boundaries between services

Services never call each other over HTTP. All coordination is mediated:

- **ingest → worker:** via the bus + Temporal workflow start (at-least-once, idempotent)
- **api → worker:** via Temporal signals/queries only
- **worker → executor:** via Temporal activity scheduling on the execution queue — and the
  executor re-derives authorization from the database, treating the task payload as untrusted
  input

This means no service mesh requirement in the MVP, no internal API auth matrix to maintain, and
every inter-service interaction is durable and replayable by construction.
