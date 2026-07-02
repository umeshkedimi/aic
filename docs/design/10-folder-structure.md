# 10. Folder Structure

## 10.1 The decision: uv workspace monorepo

One repository, structured as a **uv workspace**: five library packages under `libs/`, four
service packages under `services/`, one lockfile at the root. Each package has its own
`pyproject.toml` and declares real dependencies on its sibling packages.

| Option | Verdict | Why |
|---|---|---|
| Single package, subfolders as convention | Rejected | Boundaries by convention rot; a service image would ship all deps including write adapters — breaks the privilege model at the packaging level |
| Polyrepo (9 repos) | Rejected | Atomic cross-package changes become release-train coordination; wrong cost/benefit for one platform team |
| **uv workspace monorepo** | **Chosen** | Real package boundaries (a service installs only its dependency closure), atomic changes, single lockfile, per-service slim Docker images via `uv sync --package` |

The privilege claim from §7.1 becomes concrete here: `aic-worker`'s `pyproject.toml` depends on
`aic-integrations[readonly]`; only `aic-executor` declares the `[write]` extra. The worker image
**does not contain** the write-adapter code or its dependencies.

## 10.2 The tree

```
aic/
├── pyproject.toml               # workspace root: members, dev tooling (ruff, mypy, pytest,
├── uv.lock                      #   import-linter), no runtime deps of its own
├── Makefile                     # dev entrypoints: up, migrate, test, lint, seed
├── docker-compose.yml           # full local stack (see §30)
├── .env.example
├── .github/
│   └── workflows/               # ci.yml, cd.yml, eval.yml (see §31)
│
├── libs/
│   ├── aic-domain/              # ZERO-I/O business core (stdlib + pydantic only)
│   │   ├── pyproject.toml
│   │   ├── src/aic_domain/
│   │   │   ├── incidents/       # Incident aggregate, state machine, domain events, errors
│   │   │   ├── actions/         # closed action catalog: typed params, registry
│   │   │   ├── policy/          # policy model + deterministic PolicyEngine (pure logic)
│   │   │   ├── analysis/        # Evidence, RCA, Hypothesis value objects
│   │   │   ├── knowledge/       # Document, Chunk models
│   │   │   ├── approvals/       # ApprovalRequest/Decision, quorum rules
│   │   │   ├── ports/           # repository + gateway interfaces (Protocol classes)
│   │   │   └── shared/          # ids (uuid7), clock, common types
│   │   └── tests/
│   │
│   ├── aic-contracts/           # wire schemas (depends only on aic-domain)
│   │   ├── src/aic_contracts/
│   │   │   ├── api/v1/          # request/response models, error envelope
│   │   │   ├── events/          # bus payloads (IncidentTriggered, ...) + envelope
│   │   │   ├── agents/          # agent I/O: TriageResult, RCAResult, ProposalDraft
│   │   │   └── webhooks/        # canonical AlertEvent + per-source raw shapes
│   │   └── tests/
│   │
│   ├── aic-platform/            # runtime chassis (no business logic)
│   │   ├── src/aic_platform/
│   │   │   ├── config/          # pydantic-settings base + per-concern mixins
│   │   │   ├── logging/         # structlog JSON, correlation binding
│   │   │   ├── telemetry/       # OTel setup, Prometheus registry, LLM span helpers
│   │   │   ├── security/        # JWT verify/issue, OIDC client, API-key hashing, RBAC guard
│   │   │   ├── db/              # async engine/session factory
│   │   │   ├── redis/           # client factory, rate limiter, dedup primitives
│   │   │   └── temporal/        # client/worker factories, interceptors (tracing, logging)
│   │   └── tests/
│   │
│   ├── aic-integrations/        # ports + adapters for everything external
│   │   ├── pyproject.toml       # extras: [readonly], [write], [llm], [vector]
│   │   ├── src/aic_integrations/
│   │   │   ├── llm/             # LLMPort, OpenAI adapter (Anthropic/Azure later), structured-output retry
│   │   │   ├── vector/          # VectorStorePort, pgvector adapter
│   │   │   ├── bus/             # EventBusPort, Redis Streams adapter
│   │   │   ├── persistence/     # SQLAlchemy ORM models + repository implementations
│   │   │   ├── kubernetes/      # read/ (pods, events, deploys)  write/ (restart, rollback, scale)
│   │   │   ├── prometheus/      # read: instant + range queries
│   │   │   ├── github/          # read: deployments, PRs, diffs
│   │   │   ├── slack/           # write: messages, approval cards
│   │   │   ├── jira/            # write: issues
│   │   │   └── _base/           # AdapterBase: circuit breaker, timeout, typed errors, health
│   │   └── tests/
│   │
│   └── aic-agents/              # LangGraph reasoning (imports readonly integrations only)
│       ├── src/aic_agents/
│       │   ├── graphs/          # triage, investigation, documentation graphs
│       │   ├── tools/           # tool specs binding read-only adapters
│       │   ├── prompts/         # versioned prompt templates (hashable)
│       │   ├── parsing/         # structured-output validation + retry-with-feedback
│       │   └── budget/          # token/cost/tool-call governor
│       └── tests/
│
├── services/                    # thin composition roots: wiring, no logic
│   ├── aic-api/
│   │   ├── pyproject.toml
│   │   ├── src/aic_api/
│   │   │   ├── main.py          # entrypoint
│   │   │   ├── app.py           # FastAPI factory, DI wiring, lifespan
│   │   │   ├── middleware/      # correlation, timing, error envelope, rate limit
│   │   │   ├── routers/v1/      # incidents, approvals, knowledge, policies, admin, auth, health
│   │   │   ├── services/        # application services (use-case orchestration)
│   │   │   └── slack/           # interaction endpoint (signature verify → ApprovalService)
│   │   └── tests/
│   ├── aic-ingest/
│   │   └── src/aic_ingest/      # receivers/, normalizers/, pipeline.py (dedup→correlate→publish),
│   │                            # consumer.py (stream → start workflow), reconciler.py
│   ├── aic-worker/
│   │   └── src/aic_worker/      # workflows/incident.py (deterministic), activities/, worker.py
│   └── aic-executor/
│       └── src/aic_executor/    # gate.py, handlers/ (one per action type), verification/,
│                                # rollback.py, worker.py (execution task queue only)
│
├── migrations/                  # Alembic (env.py targets aic_integrations.persistence models)
│   └── versions/
├── deploy/
│   ├── docker/                  # Dockerfile (multi-stage, multi-target: one target per service)
│   ├── k8s/                     # kustomize: base/ + overlays/{dev,prod}  (see §29)
│   ├── grafana/                 # provisioned dashboards (JSON, in repo)
│   └── prometheus/
├── evals/                       # evaluation harness (see §22): datasets/, scorers/, run.py
├── scripts/                     # seed_knowledge.py, generate_incident.py (demo/testing data)
├── tests/
│   └── e2e/                     # cross-service flows against docker-compose stack
└── docs/
    ├── design/                  # documents 01–40 (this series)
    ├── adr/                     # numbered architecture decision records
    └── runbooks/                # runbooks for operating AIC itself
```

## 10.3 Rules that keep the structure honest

1. **Tests live with their package** (`libs/aic-domain/tests/`), not in a parallel tree — a
   package is releasable exactly when *its own* tests pass. Only cross-service `e2e/` sits at the
   root.
2. **`services/*/src` contain wiring only.** If a file in a service grows business logic, it's a
   review finding; logic belongs in `libs/`. Heuristic: a service package should be small enough
   to read in one sitting.
3. **Import contracts are CI-enforced** (import-linter): the §7.1 dependency matrix is config,
   not documentation — e.g. `aic_agents` importing `aic_integrations.kubernetes.write` fails the
   build.
4. **One Dockerfile, multiple targets.** `docker build --target aic-worker` produces an image
   with only that service's dependency closure (`uv sync --package aic-worker --no-dev`).
5. **Dashboards, alerts, manifests are code.** Grafana JSON, Prometheus rules, and kustomize
   overlays are reviewed in PRs like everything else.
