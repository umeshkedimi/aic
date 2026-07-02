# 5. Non-Functional Requirements

NFRs carry measurable targets; anything unmeasurable is a wish, not a requirement. IDs follow
`NFR-<area>.<n>`.

## NFR-1 Reliability & availability

| ID | Requirement | Target |
|---|---|---|
| NFR-1.1 | Control-plane availability (`aic-api`, `aic-ingest`) | 99.9% monthly |
| NFR-1.2 | No accepted alert is ever lost | Persist-before-ack; at-least-once from ingest to workflow start |
| NFR-1.3 | Incident workflows survive process crashes, deploys, and infra restarts | Zero lost workflow state (Temporal durability); resume within 60s of worker recovery |
| NFR-1.4 | Degraded-dependency behavior defined for every external dependency (LLM down, integration down, Redis down) | Documented + tested fallback per dependency; investigation continues with partial evidence |
| NFR-1.5 | AIC outage never blocks paging or manual response | Paging path is upstream and independent by design |

## NFR-2 Performance

| ID | Requirement | Target |
|---|---|---|
| NFR-2.1 | Webhook acknowledgment latency | p99 < 500 ms |
| NFR-2.2 | Alert → incident created + workflow started | p95 < 5 s |
| NFR-2.3 | Alert → triage classification available | p95 < 60 s |
| NFR-2.4 | Alert → first RCA hypothesis with evidence | p95 < 3 min (standard depth) |
| NFR-2.5 | Approval decision → execution start | p95 < 10 s |
| NFR-2.6 | API read endpoints | p99 < 300 ms |

## NFR-3 Scalability

| ID | Requirement | Target |
|---|---|---|
| NFR-3.1 | Sustained alert ingestion | 100 alerts/s sustained, 500/s burst (storm scenario), backpressure via stream lag rather than rejection |
| NFR-3.2 | Concurrent active investigations | 200+ (workers scale horizontally; LLM concurrency is the governed bottleneck) |
| NFR-3.3 | All services stateless and horizontally scalable; state only in Postgres/Redis/Temporal | HPA-compatible; no sticky sessions |
| NFR-3.4 | Data volume | 1M incidents, 100M events, 10M vector chunks without schema redesign (partitioning strategy documented) |

## NFR-4 Security

| ID | Requirement | Target |
|---|---|---|
| NFR-4.1 | Least privilege per plane | Worker: read-only integration creds. Executor: write creds scoped to action catalog. API/ingest: none. Enforced via separate service accounts + K8s NetworkPolicies |
| NFR-4.2 | No secrets in code, images, logs, or LLM prompts | Secrets injected at runtime (K8s secrets → external secret manager); secret-scanning in CI; prompt redaction filter |
| NFR-4.3 | All external input treated as untrusted — including tool outputs fed to the LLM (prompt-injection surface) | Injection-resistant prompt assembly; agent output only actionable through typed catalog + policy gate |
| NFR-4.4 | Transport encryption everywhere; tokens are short-lived | TLS 1.2+; JWT ≤ 1 h; refresh via OIDC |
| NFR-4.5 | Webhook authenticity | Per-source HMAC/shared secret + replay protection (timestamp window) |
| NFR-4.6 | Dependency and image hygiene | CVE scan (critical=block) in CI; pinned digests; non-root containers |

## NFR-5 Auditability & compliance

| ID | Requirement | Target |
|---|---|---|
| NFR-5.1 | Every state-changing operation attributable to a human identity, service identity, or policy rule | 100%, no exceptions path |
| NFR-5.2 | Incident event log is append-only | No UPDATE/DELETE grants on event tables for application roles |
| NFR-5.3 | Full incident reconstruction (evidence, prompts metadata, decisions, actions) available | For the retention window: 400 days events, 90 days full LLM traces |
| NFR-5.4 | Records exportable in a compliance-consumable format | JSON + Markdown export API |

## NFR-6 Observability

| ID | Requirement | Target |
|---|---|---|
| NFR-6.1 | Distributed tracing across ingest → workflow → agent → tools → executor | 100% of incidents traced end-to-end (OTel), one trace per incident |
| NFR-6.2 | LLM-specific telemetry (model, tokens, cost, latency, eval scores) | 100% of calls → Langfuse + Prometheus metrics |
| NFR-6.3 | RED metrics for every service, USE metrics for workers/queues | Grafana dashboards checked into repo |
| NFR-6.4 | Alerting on AIC's own health | Burn-rate SLO alerts; "who watches the watcher" runbook |
| NFR-6.5 | Structured JSON logs with correlation (trace ID, incident ID) on every line | 100%; human-readable rendering in dev only |

## NFR-7 Cost governance

| ID | Requirement | Target |
|---|---|---|
| NFR-7.1 | LLM spend attributable per incident, per agent role, per model | Cost dimension on all LLM telemetry |
| NFR-7.2 | Hard per-incident budget with graceful cutoff | Default $2/incident standard depth (configurable); cutoff → partial results + escalation, never silent truncation |
| NFR-7.3 | Tiered model routing | Cheap/fast model for triage & summarization; frontier model only for RCA reasoning |
| NFR-7.4 | Global monthly budget alarm | Prometheus alert at 80% of configured budget |

## NFR-8 Maintainability & code quality

| ID | Requirement | Target |
|---|---|---|
| NFR-8.1 | Typed Python everywhere | mypy strict, CI-gated; Pydantic v2 at all boundaries |
| NFR-8.2 | Test coverage | ≥ 85% on domain + application layers; integration tests for every adapter; e2e happy-path + failure-path suites |
| NFR-8.3 | Lint/format uniformity | ruff (lint + format), CI-gated |
| NFR-8.4 | Every architecturally significant decision recorded | ADRs in `docs/adr/`, numbered, immutable |
| NFR-8.5 | A new engineer can run the full stack locally | `docker compose up` + one bootstrap command, documented, < 15 min |

## NFR-9 Portability & extensibility

| ID | Requirement | Target |
|---|---|---|
| NFR-9.1 | LLM provider swap (OpenAI ↔ Anthropic ↔ Azure) | Config-only for agents; new provider = one adapter implementing `LLMPort` |
| NFR-9.2 | Vector store swap (pgvector → Pinecone/Qdrant/Weaviate) | One adapter implementing `VectorStorePort`; no agent/domain changes |
| NFR-9.3 | Message bus swap (Redis Streams → Kafka) | One adapter implementing `EventBusPort` |
| NFR-9.4 | New integration adapter | Implement interface + register + configure creds; no core changes; target ≤ 2 days effort |
| NFR-9.5 | New action type | Typed action + executor handler + policy entry + tests; no workflow changes |

## NFR-10 Data management

| ID | Requirement | Target |
|---|---|---|
| NFR-10.1 | Schema migrations are forward-only, reviewed, and applied via CI/CD (Alembic) | Zero manual DDL in any environment |
| NFR-10.2 | PII/sensitive data minimization in stored evidence and prompts | Redaction filter before persistence; documented data inventory |
| NFR-10.3 | Backup/restore for Postgres; documented RPO/RTO | RPO ≤ 15 min, RTO ≤ 1 h (deployment-profile dependent, documented) |
