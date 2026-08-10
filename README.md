# AIC — Agentic Incident Command

**A production-grade Agentic Reliability Platform for detecting, investigating, and safely
remediating production incidents.**

[![Status](https://img.shields.io/badge/status-design%20phase-blue)](docs/design/01-signature-incident-lifecycle.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB)](docs/design/01-signature-incident-lifecycle.md)

---

## Overview

AIC is an autonomous reliability control loop, not a chatbot bolted onto an alerting tool. It
observes production systems, correlates raw signals into incidents, investigates root cause using
real evidence from metrics/logs/deployments, generates explainable root-cause hypotheses,
proposes remediation behind policy guardrails, requires human approval for anything risky,
executes and verifies recovery, and learns from every incident it handles:

```
OBSERVE → DETECT → CORRELATE → INVESTIGATE → REASON → FORM RCA → PLAN REMEDIATION
        → APPLY POLICY → APPROVE → ACT → VERIFY → RESOLVE → LEARN
```

The project's engineering standard: **deterministic code for deterministic operations, LLM
reasoning only where it's genuinely valuable** — evidence interpretation, hypothesis generation,
remediation judgment. Correlation, policy enforcement, execution, and verification are plain,
auditable code. No LLM output ever reaches a write credential without passing through typed
validation, a policy engine, and — where policy requires it — a named human approval.

## Why one scenario, not many integrations

This project is built depth-first: one incident scenario, engineered completely and correctly,
rather than broad shallow coverage across many alert sources and agents. The signature scenario —
a bad deployment causing latency, 5xx errors, database connection pool exhaustion, and downstream
checkout failures — is specified end-to-end, stage by stage, before any breadth is added. See
[`docs/design/01-signature-incident-lifecycle.md`](docs/design/01-signature-incident-lifecycle.md).

## Architecture at a glance

| Concern | Choice | Why |
|---|---|---|
| Agent orchestration | LangGraph, wrapping an explicitly-designed graph | [ADR 0001](docs/adr/0001-langgraph-for-investigation-orchestration.md) — the framework executes a graph we designed; it doesn't decide the architecture |
| Event propagation | Kafka (`alert-events` topic) | [ADR 0002](docs/adr/0002-kafka-for-alert-event-propagation.md) |
| Remediation target | Local Kubernetes (`kind`) | [ADR 0003](docs/adr/0003-kubernetes-remediation-target.md) — real `kubectl rollout undo`, real RBAC-scoped credentials |
| LLM access | LiteLLM gateway behind a provider-agnostic `LLMPort` | [ADR 0004](docs/adr/0004-litellm-gateway-for-llm-access.md) |
| System of record | PostgreSQL | Incidents, evidence, RCA, approvals, policy decisions, audit log |
| Knowledge store | Qdrant | Postmortems and runbooks, retrieved during investigation |
| Observability | OpenTelemetry, Prometheus, Grafana, Loki | One trace per incident, span per stage/tool/LLM call |

Full rationale for every non-obvious choice is in [`docs/adr/`](docs/adr/); the complete system
design — topology, data model, state machine, node contracts, privilege separation — is in
[`docs/design/`](docs/design/).

## Documentation

| Document | Covers |
|---|---|
| [`docs/design/01-signature-incident-lifecycle.md`](docs/design/01-signature-incident-lifecycle.md) | The full end-to-end design: scenario, topology, stage-by-stage breakdown, domain model, state machine, LangGraph graph, Kafka schema, privilege separation, production-grade concerns |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records for each non-obvious tradeoff |

## Project status

**Design phase.** The signature incident lifecycle is fully specified; implementation has not
started. Work proceeds as a dependency-ordered backlog (repo foundation → toy services → real
observability stack → ingest/correlation → investigation graph → remediation/policy →
approval → execution → verification → learning loop → AIC's own observability → end-to-end
hardening), each stage built against real infrastructure and real failure conditions rather than
mocked data.

## Engineering principles

- Explicit state machines over uncontrolled agent loops — every incident transition is a pure,
  unit-tested function; nothing is inferred from LLM output.
- Agents propose, policy and humans dispose — an LLM never has a code path to a write credential.
- Privilege separation is enforced by infrastructure (K8s RBAC, distinct service accounts), not by
  convention.
- Every tool call and LLM call has an explicit timeout; every action is idempotent; every write
  passes through structured validation.
- Every architectural decision has a documented reason (see `docs/adr/`).

## License

MIT — see [`LICENSE`](LICENSE).
