# 4. Functional Requirements

Requirements use stable IDs (`FR-<area>.<n>`) so code, tests, and ADRs can trace back to them.
Priority: **M** = Must (MVP), **S** = Should (post-MVP hardening), **C** = Could (roadmap).

## FR-1 Alert ingestion

| ID | Requirement | Priority |
|---|---|---|
| FR-1.1 | Receive and authenticate webhooks from Prometheus Alertmanager (HMAC/shared secret per source) | M |
| FR-1.2 | Receive Datadog and CloudWatch (SNS) alert webhooks | S |
| FR-1.3 | Normalize all sources into a canonical `AlertEvent` schema (source, service, severity, labels, timestamps, raw payload preserved) | M |
| FR-1.4 | Deduplicate repeat alerts within a configurable window (fingerprint-based, Redis) | M |
| FR-1.5 | Correlate related alerts into a single incident (time window + shared labels; dependency topology later) | S |
| FR-1.6 | Support manual incident creation via API (with the same downstream flow) | M |
| FR-1.7 | Ack webhooks fast and process asynchronously; never lose an accepted alert (persist before ack) | M |

## FR-2 Incident management

| ID | Requirement | Priority |
|---|---|---|
| FR-2.1 | Incident lifecycle state machine: `open → triaging → investigating → awaiting_approval → remediating → verifying → resolved / closed`, with `escalated` and `failed` branches; illegal transitions rejected | M |
| FR-2.2 | Every state change, observation, tool call, proposal, approval, and action recorded as an immutable `IncidentEvent` (append-only) | M |
| FR-2.3 | CRUD-with-rules API: list/filter/get incidents; state mutations only through domain operations (no raw PATCH of status) | M |
| FR-2.4 | Severity model (SEV-1..4) assigned at triage, human-overridable, override recorded | M |
| FR-2.5 | Link incidents to external artifacts (Jira issue, GitHub PR, Slack channel) | S |

## FR-3 Triage & investigation (agents)

| ID | Requirement | Priority |
|---|---|---|
| FR-3.1 | Triage agent classifies severity, affected service(s), customer impact, and investigation depth within a bounded token/time budget | M |
| FR-3.2 | Investigation agent gathers evidence via read-only tools: K8s state, Prometheus queries, recent deployments, GitHub diffs, prior incidents (RAG) | M |
| FR-3.3 | Every tool invocation captured as typed `Evidence` with source, query, result digest, latency, and timestamp | M |
| FR-3.4 | Agent steps are bounded: max tool calls, max tokens, max wall-clock per phase; exceeding budget yields partial results + escalation, never a hang | M |
| FR-3.5 | RCA output is a typed `RootCauseAnalysis`: ranked hypotheses, each with confidence ∈ [0,1] and citations to Evidence IDs | M |
| FR-3.6 | Investigation degrades gracefully: if an integration is down, proceed with remaining sources and record the gap | M |

## FR-4 Remediation proposals & policy

| ID | Requirement | Priority |
|---|---|---|
| FR-4.1 | Proposals reference only actions from the closed action catalog (typed parameters, JSON-schema validated) | M |
| FR-4.2 | Initial catalog: `RestartDeployment`, `RollbackRelease`, `ScaleDeployment`, `CordonNode`, `SilenceAlert`, `CreateJiraIssue`, `PostSlackUpdate` | M |
| FR-4.3 | Policy engine evaluates each action → `auto_approve` / `require_approval(quorum, roles)` / `forbid`, keyed on action class × environment × blast-radius attributes | M |
| FR-4.4 | Policies are versioned data (Postgres), auditable, hot-reloadable; every decision records the matching rule + policy version | M |
| FR-4.5 | Dry-run support per action type; dry-run output attached to the approval request | S |
| FR-4.6 | Forbidden actions are also blocked in `aic-executor` independently (defense in depth) | M |

## FR-5 Human approval workflow

| ID | Requirement | Priority |
|---|---|---|
| FR-5.1 | Approval requests delivered via Slack (interactive buttons) and web API, showing evidence summary, action details, dry-run output, and blast radius | M |
| FR-5.2 | Approve/reject requires an authenticated identity with the required role; quorum configurable per policy | M |
| FR-5.3 | Workflow waits durably (Temporal signal); configurable timeout → escalation chain → safe expiry (no action) | M |
| FR-5.4 | Approver can reject-with-reason; reason is fed back to the agent for one bounded re-proposal cycle | S |
| FR-5.5 | All approval activity (request, view, decision, timeout, escalation) is audit-logged with identity and timestamp | M |

## FR-6 Execution & verification

| ID | Requirement | Priority |
|---|---|---|
| FR-6.1 | Only `aic-executor` executes actions; it independently re-validates policy + approval before acting | M |
| FR-6.2 | Actions are idempotent (idempotency key per action instance); safe under Temporal activity retry | M |
| FR-6.3 | Per-action timeout, structured result capture, and failure classification (retryable / fatal) | M |
| FR-6.4 | Verification probes re-check the triggering alert condition and service health over a configurable soak window | M |
| FR-6.5 | Failed verification triggers the rollback path (if defined for the action) and human escalation | M |
| FR-6.6 | Concurrency guard: one active remediation per target resource at a time (lock in Postgres) | S |

## FR-7 Knowledge & RAG

| ID | Requirement | Priority |
|---|---|---|
| FR-7.1 | Ingest runbooks/postmortems (Markdown) via API: chunk, embed, store in pgvector with metadata (service, failure mode, source, version) | M |
| FR-7.2 | Semantic retrieval with metadata filtering; results carry similarity scores and are cited in RCA | M |
| FR-7.3 | Resolved incidents are automatically summarized and indexed into the corpus | S |
| FR-7.4 | Documents are versioned; retrieval uses only active versions | S |

## FR-8 Post-incident

| ID | Requirement | Priority |
|---|---|---|
| FR-8.1 | On resolution, generate a structured post-incident record from the event log: timeline, evidence, RCA, actions, approvals, verification, durations per phase | M |
| FR-8.2 | File the record to Jira and/or a GitHub repo (configurable) | S |
| FR-8.3 | Export incident records (JSON/Markdown) via API for compliance | M |

## FR-9 Integrations

| ID | Requirement | Priority |
|---|---|---|
| FR-9.1 | Adapter interface with declared capability (read/write), health check, and per-adapter credential config | M |
| FR-9.2 | MVP adapters — read: Kubernetes, Prometheus, GitHub; write: Kubernetes (restart/rollback/scale), Slack, Jira | M |
| FR-9.3 | Post-MVP adapters: Grafana, Datadog, CloudWatch (read); GitHub (write); PagerDuty | S |
| FR-9.4 | Adapter failures are isolated: circuit breaker + typed error; never crash an investigation | M |

## FR-10 AuthN/Z, admin & audit

| ID | Requirement | Priority |
|---|---|---|
| FR-10.1 | OAuth2/OIDC login for humans; JWT bearer for API; scoped API keys for machine callers (webhook sources, CI) | M |
| FR-10.2 | RBAC roles: `viewer`, `operator`, `approver`, `admin` (role → permission mapping, extensible) | M |
| FR-10.3 | Admin API for policies, integrations, sources, and RBAC assignments — all changes audit-logged | M |
| FR-10.4 | Immutable audit trail queryable by incident, actor, action type, and time range | M |

## FR-11 LLM operations

| ID | Requirement | Priority |
|---|---|---|
| FR-11.1 | All LLM access through `LLMPort`; provider/model selectable per agent role via config (OpenAI MVP; Anthropic, Azure OpenAI without code change to agents) | M |
| FR-11.2 | Structured outputs validated against Pydantic schemas; bounded retry-with-feedback on validation failure | M |
| FR-11.3 | Per-call capture: model, tokens in/out, cost, latency — exported to Langfuse and Prometheus | M |
| FR-11.4 | Per-incident LLM budget (tokens/cost) with hard cutoff → partial-result escalation | M |
| FR-11.5 | Offline evaluation harness: curated incident dataset, scored RCA/triage quality, run in CI | S |
