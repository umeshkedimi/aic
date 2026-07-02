# 7. Component Diagram

Section [6.3](06-architecture.md#63-component-diagram) shows the *container* level (C4 L2): four
services and their infrastructure. This document zooms into C4 level 3: the components inside each
service, the shared packages they are built from, and the dependency rules between them.

## 7.1 Monorepo package layout

One repository, four deployable services, five shared packages. Services are thin composition
roots; almost all logic lives in packages so it is testable in isolation and shared without
duplication.

```mermaid
graph TD
    subgraph services ["Deployable services (composition roots)"]
        API[aic-api]
        ING[aic-ingest]
        WRK[aic-worker]
        EXE[aic-executor]
    end

    subgraph packages ["Shared packages (src/)"]
        DOM["<b>aic_domain</b><br/>entities, value objects,<br/>state machine, domain events,<br/>action catalog, policy model"]
        CON["<b>aic_contracts</b><br/>API schemas, event bus payloads,<br/>agent I/O schemas (Pydantic v2)"]
        PLT["<b>aic_platform</b><br/>settings, logging, OTel,<br/>db/redis/temporal clients,<br/>auth primitives"]
        INT["<b>aic_integrations</b><br/>ports + adapters:<br/>LLM, vector, event bus,<br/>K8s, Prometheus, GitHub,<br/>Slack, Jira"]
        AGT["<b>aic_agents</b><br/>LangGraph graphs, prompts,<br/>tool bindings, output parsing"]
    end

    API --> DOM & CON & PLT & INT
    ING --> DOM & CON & PLT
    WRK --> DOM & CON & PLT & INT & AGT
    EXE --> DOM & CON & PLT & INT

    AGT --> DOM & CON & INT
    INT --> DOM & CON & PLT
    CON --> DOM
    PLT -.-> DOM
```

**Dependency rules (enforced by import-linter in CI):**

1. `aic_domain` imports nothing but the standard library and Pydantic. No I/O, no framework, no
   clients. This is what makes the business rules unit-testable in milliseconds.
2. `aic_contracts` depends only on `aic_domain` (schemas wrap domain types for the wire).
3. `aic_integrations` defines **ports** (abstract interfaces) alongside **adapters**
   (implementations). Consumers import ports; composition roots bind adapters.
4. `aic_agents` never imports write-capable adapters — the package literally cannot construct
   them (they live behind an executor-only extra). Privilege separation starts at import time.
5. Services import packages; packages never import services.

## 7.2 `aic-api` — control plane

```mermaid
graph LR
    subgraph aic-api
        MW["Middleware<br/>correlation · timing ·<br/>error envelope · rate limit"]
        RT["Routers (v1)<br/>incidents · approvals ·<br/>knowledge · policies ·<br/>admin · auth · health"]
        AUTH["Auth<br/>OIDC login · JWT issue/verify ·<br/>API keys · RBAC guard"]
        SVC["Application services<br/>IncidentQueryService<br/>ApprovalService<br/>KnowledgeService<br/>PolicyAdminService"]
        SLACK["Slack interaction handler<br/>(button callbacks, signatures)"]
        REPO["Repositories<br/>(async SQLAlchemy)"]
        TC["Temporal client<br/>(start · signal · query)"]
    end
    RT --> AUTH
    RT --> SVC
    SLACK --> AUTH
    SLACK --> SVC
    SVC --> REPO
    SVC --> TC
    MW --> RT
```

Key point: `aic-api` never talks to an LLM and never holds integration credentials (Slack
signing secret excepted — it verifies inbound interaction payloads). Approvals are delivered to
running workflows as **Temporal signals**; the API is stateless.

## 7.3 `aic-ingest` — ingestion pipeline

```mermaid
graph LR
    subgraph aic-ingest
        WH["Webhook receivers<br/>/alertmanager · /datadog ·<br/>/cloudwatch (per-source auth)"]
        NORM["Normalizers<br/>source payload → AlertEvent"]
        PERSIST["Alert store<br/>(persist before ack)"]
        DEDUP["Deduplicator<br/>fingerprint window (Redis)"]
        CORR["Correlator<br/>attach to open incident<br/>or create new"]
        PUB["Publisher<br/>IncidentTriggered →<br/>Redis Streams"]
        CONS["Stream consumer<br/>(consumer group) →<br/>start IncidentWorkflow"]
    end
    WH --> NORM --> PERSIST --> DEDUP --> CORR --> PUB
    PUB -.->|stream| CONS
```

The consumer is co-located but logically separate: at-least-once delivery + an idempotent
workflow-start (Temporal workflow ID = incident ID) means duplicate deliveries are harmless.

## 7.4 `aic-worker` — intelligence plane

```mermaid
graph LR
    subgraph aic-worker
        WF["IncidentWorkflow<br/>(Temporal, deterministic:<br/>phases, timers, signals)"]
        ACT["Activities<br/>triage · investigate · rca ·<br/>propose · document"]
        GRAPHS["LangGraph graphs<br/>(from aic_agents)"]
        TOOLS["Tool registry<br/>read-only bindings"]
        RAG["RAG pipeline<br/>retrieve · assemble context"]
        LLMP["LLMPort<br/>OpenAI adapter (MVP)"]
        POL["PolicyEngine<br/>(deterministic, in-process)"]
        BUD["Budget governor<br/>tokens · cost · tool calls"]
    end
    WF --> ACT
    ACT --> GRAPHS
    GRAPHS --> TOOLS
    GRAPHS --> LLMP
    GRAPHS --> BUD
    ACT --> RAG
    ACT --> POL
```

The workflow itself contains **no I/O and no LLM calls** — Temporal requires workflow code to be
deterministic. All side effects live in activities; the workflow sequences them, holds timers,
and waits on approval signals.

## 7.5 `aic-executor` — execution plane

```mermaid
graph LR
    subgraph aic-executor
        EWK["Temporal worker<br/>(dedicated 'execution'<br/>task queue only)"]
        GATE["Independent gate<br/>re-verify policy decision +<br/>approval record from DB"]
        HAND["Action handlers<br/>one per catalog action type<br/>(typed params in, result out)"]
        WAD["Write adapters<br/>K8s · Slack · Jira<br/>(scoped credentials)"]
        VER["Verification probes<br/>re-run alert query · health checks<br/>over soak window"]
        RB["Rollback coordinator"]
    end
    EWK --> GATE --> HAND --> WAD
    EWK --> VER
    VER -->|failure| RB --> HAND
```

The gate re-reads the approval and policy decision from Postgres and re-evaluates policy before
touching anything — it does not trust its own caller. If `aic-worker` is compromised end to end,
the worst it can submit is a catalog action that still has to pass this gate.

## 7.6 Cross-cutting components

| Component | Lives in | Used by | Notes |
|---|---|---|---|
| `Settings` (pydantic-settings) | `aic_platform` | all | Per-service settings classes; fail-fast validation at boot |
| Structured logging (structlog → JSON) | `aic_platform` | all | trace_id + incident_id bound into every line |
| OTel tracing + Prometheus metrics | `aic_platform` | all | one trace per incident; LLM spans enriched |
| `EventBusPort` (Redis Streams adapter) | `aic_integrations` | ingest, api | Kafka adapter is a future drop-in |
| `VectorStorePort` (pgvector adapter) | `aic_integrations` | worker, api | Pinecone/Qdrant/Weaviate later |
| Redaction filter | `aic_platform` | ingest, worker | strips secrets/PII before persistence and before prompts |
