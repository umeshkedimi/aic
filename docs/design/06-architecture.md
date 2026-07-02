# 6. Overall Architecture

## 6.1 Architectural principles

Each principle exists because a specific failure mode makes naive designs fall over in production:

1. **Four planes, separately deployable.** *Control plane* (API, auth, approvals), *intelligence
   plane* (agents, RAG, LLM access), *execution plane* (guarded write actions), *data plane*
   (Postgres, Redis, Temporal). They scale differently (agents are LLM-latency-bound, ingestion is
   bursty), fail differently, and have radically different privilege requirements.
2. **Durable-by-default.** Anything that outlives one HTTP request lives in Temporal or Postgres,
   never in process memory. This is what makes "wait 4 hours for approval, then continue exactly
   where we left off" trivial instead of a distributed-systems project.
3. **Typed contracts at every boundary.** Pydantic v2 models for every API payload, every tool
   input/output, every agent artifact, every event on the bus. LLM output is parsed into these
   models or rejected — free-text never crosses a service boundary.
4. **Agents propose, policies dispose.** The load-bearing security principle: agents emit typed
   proposals; a deterministic policy engine and (where required) named humans decide.
5. **Least privilege per plane.** Read credentials in the intelligence plane, write credentials
   only in the execution plane, no infra credentials at all in the control plane.
6. **Ports and adapters everywhere externals touch the system.** `LLMPort`, `VectorStorePort`,
   `EventBusPort`, and one `IntegrationAdapter` per external tool. Enterprise reality: every
   customer has a different observability stack; adapters are the extension point.
7. **Observable by default.** One trace per incident spanning ingest → workflow → every agent step
   → every tool call → execution → verification, with LLM spans enriched (model, tokens, cost) and
   exported to both the ops stack (Grafana) and the LLMOps stack (Langfuse).
8. **Monorepo, multiple deployables.** One repo, shared domain/contracts packages, four service
   images. Microservice boundaries where privilege or scaling demands them — nowhere else.

## 6.2 System topology

Four deployable services plus shared infrastructure:

| Service | Plane | Responsibility | Credentials it holds |
|---|---|---|---|
| `aic-api` | Control | REST API, AuthN/Z, incident & approval management, Slack interactions, admin/config | Postgres, Redis, Temporal client |
| `aic-ingest` | Control | Webhook receivers (Alertmanager/Datadog/CloudWatch), normalization, dedup/correlation, publishes to Redis Streams | Postgres, Redis |
| `aic-worker` | Intelligence | Temporal workers running the investigation workflow; LangGraph agents; RAG over runbooks/past incidents | **Read-only** integration creds, LLM API key, pgvector |
| `aic-executor` | Execution | Executes approved `ActionPlan`s via guarded adapters; verification probes; rollback | **Write** integration creds (scoped), Temporal worker on a dedicated task queue |

## 6.3 Component diagram

```
                    ┌────────────────────────── ALERT SOURCES ──────────────────────────┐
                    │   Alertmanager        Datadog         CloudWatch        Manual     │
                    └───────┬───────────────────┬───────────────┬───────────────┬───────┘
                            │ webhooks                          │               │
                            ▼                                   ▼               ▼
                    ┌───────────────┐                    ┌─────────────────────────────┐
                    │  aic-ingest   │                    │           aic-api           │
                    │  normalize    │                    │  REST / OpenAPI             │
                    │  dedup        │                    │  AuthN (OAuth2/JWT)         │
                    │  correlate    │                    │  AuthZ (RBAC)               │
                    └───────┬───────┘                    │  Approvals  ◄────► Slack    │
                            │ IncidentTriggered          │  Admin / Policy config      │
                            ▼                            └────────┬────────────────────┘
                 ╔══ Redis Streams ══╗                            │ start / signal
                            │                                     ▼
                            │                        ╔═══════════════════════╗
                            └───────────────────────►║        TEMPORAL       ║
                                                     ║  IncidentWorkflow     ║
                                                     ║  (durable state)      ║
                                                     ╚═══╤═══════════════╤═══╝
                              investigation task queue   │               │   execution task queue
                                                         ▼               ▼
                              ┌──────────────────────────────┐   ┌──────────────────────────┐
                              │         aic-worker           │   │       aic-executor       │
                              │  LangGraph agents:           │   │  Policy re-check (defense │
                              │   triage → investigate →     │   │   in depth)              │
                              │   hypothesize → propose      │   │  Guarded write adapters  │
                              │  RAG: runbooks, postmortems  │   │  Verification probes     │
                              │  READ-ONLY tool adapters ────┼─┐ │  Rollback               │
                              │  LLMPort (OpenAI│Anthropic)  │ │ │  WRITE adapters ─────────┼─┐
                              └──────────────┬───────────────┘ │ └──────────────────────────┘ │
                                             │                 ▼                              ▼
                                             │        ┌─────────────────────────────────────────┐
                                             │        │ K8s API · Prometheus · Grafana · Datadog │
                                             │        │ CloudWatch · GitHub · Jira · Slack       │
                                             │        └─────────────────────────────────────────┘
                                             ▼
        ┌────────────────────────────── DATA PLANE ──────────────────────────────┐
        │  PostgreSQL (incidents, events, approvals, policies) + pgvector (RAG)  │
        │  Redis (cache, rate limits, streams)                                   │
        └────────────────────────────────────────────────────────────────────────┘

        ┌─────────────────────────── OBSERVABILITY PLANE ────────────────────────┐
        │  OTel Collector → Prometheus/Grafana (ops)  +  Langfuse/Phoenix (LLM)  │
        └────────────────────────────────────────────────────────────────────────┘
```

## 6.4 The incident lifecycle

The one flow everything serves:

1. **Ingest.** Alertmanager fires a webhook → `aic-ingest` authenticates it, normalizes to a
   canonical `AlertEvent`, deduplicates (Redis), correlates into a new or existing incident,
   publishes `IncidentTriggered`.
2. **Workflow start.** A consumer starts a Temporal `IncidentWorkflow` — the single durable source
   of truth for this incident's progress from here on.
3. **Triage.** Fast, cheap LLM pass: classify severity/service/blast radius; decide investigation
   depth. Auto-page humans immediately if classified critical — AIC assists, it never gates paging.
4. **Investigation.** The LangGraph investigation agent runs inside a Temporal activity: pulls pod
   states, recent deploys, metrics anomalies, recent PRs, related past incidents (RAG) using
   read-only tools. Every tool call is logged as an incident event with inputs, outputs, latency,
   and cost.
5. **RCA.** Structured `RootCauseAnalysis`: ranked hypotheses, each with confidence and *citations
   to gathered evidence* (evidence IDs, not vibes).
6. **Proposal.** Structured `RemediationProposal` of typed actions drawn from a closed action
   catalog (`RestartDeployment`, `RollbackRelease`, `ScaleDeployment`, …). Free-text commands are
   not an action type.
7. **Policy gate.** Deterministic policy engine evaluates each action: `auto_approve` /
   `require_approval(n approvers, roles)` / `forbid` — keyed on action class × environment ×
   blast radius.
8. **Approval.** Workflow pauses on a Temporal signal. Approvers act via Slack buttons or the API;
   identity, timestamp, and decision are recorded. Timeout escalates, then expires safely.
9. **Execution.** Approved plans go to `aic-executor` on a separate task queue. It **re-validates
   policy independently** (defense in depth), executes via guarded write adapters with per-action
   timeouts, and records results.
10. **Verification.** Probes re-check the original alert condition and service health for a soak
    period. Failure → rollback path and human escalation. Success → resolution.
11. **Post-incident.** Agent drafts the incident record — timeline, RCA, actions, approvals —
    files it to Jira/GitHub, and indexes it into the RAG store *so the platform gets smarter with
    every incident it handles*.

## 6.5 Key decisions and rationale

| Decision | Alternative | Why |
|---|---|---|
| Temporal for orchestration | Celery/RQ, custom state machine | Investigations are long-lived (minutes–hours), must survive crashes and waits for human approval. Temporal gives replay-durable state, signals for HITL, per-activity retry policies, and infinite audit of workflow history. Celery gives you a task queue and a prayer. |
| LangGraph *inside* Temporal activities | LangGraph checkpointing as the durability layer | Clear split: Temporal owns *durability and time* (retries, timers, signals); LangGraph owns *reasoning topology* within a bounded step. Agent steps become idempotent activities; a crashed reasoning step replays from the workflow, not from a fragile checkpoint store. |
| Separate `aic-executor` service | Execute from the agent worker | Privilege separation is only real at a process/credential boundary. This turns "the agent can't be prompt-injected into deleting prod" from a hope into a property. |
| Closed action catalog | Agent generates shell/kubectl commands | Typed actions are policy-checkable, dry-runnable, auditable, and testable. Arbitrary command generation is unreviewable and unbounded blast radius. |
| pgvector first | Qdrant/Pinecone day one | One less stateful system to operate; Postgres transactional consistency between incident data and embeddings; `VectorStorePort` keeps the exit door open. |
| Redis Streams first | Kafka day one | Consumer groups + at-least-once delivery cover current volume; Kafka's operational cost isn't justified until throughput demands it. `EventBusPort` abstracts it. |
| Monorepo, 4 images | Polyrepo microservices | Shared Pydantic contracts and domain code with atomic cross-service changes; deployment boundaries only where privilege/scaling require them. |
| Policy engine in-process (Python, versioned rules in Postgres) | OPA/Cedar day one | Start with a deterministic, unit-testable rules engine owning a small decision space; the interface mirrors OPA's input/decision shape so migrating later is mechanical. |
