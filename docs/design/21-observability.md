# 21. Observability

Observability here answers two different audiences: **operators** ("is AIC healthy?") and
**AI engineers** ("is AIC *smart*, and what did it cost?"). One instrumentation layer feeds
both — OTel is the single emission API; backends differ per audience.

```
services ──OTel SDK──► OTel Collector ──┬──► Tempo/Jaeger   (traces, ops)
                                        ├──► Prometheus     (metrics)
                                        └──► Loki           (logs)
LLM spans (enriched) ──Langfuse SDK──────► Langfuse         (LLM traces, cost, prompts)
eval runs ───────────────────────────────► Phoenix          (experiment analysis, drift)
```

Langfuse gets *LLM-shaped* data (prompts, completions, token/cost, scores) linked to OTel
traces by shared IDs; Phoenix consumes eval-harness output (§22). Neither replaces the ops
stack; they answer questions Grafana can't ("show me all RCAs where hypothesis #1 changed after
self-check").

## 21.1 The trace model: one incident = one trace

The incident ID is the trace's anchor; every span carries `aic.incident_id`. Span taxonomy
(names are the contract — dashboards and alerts key on them):

```
incident                              (root, ~minutes–hours, ends at resolved)
├── ingest.receive / normalize / correlate
├── workflow.phase.triaging
│   └── agent.triage
│       └── llm.call {model, tokens_in/out, cost_usd, cache_hit}
├── workflow.phase.investigating
│   └── agent.investigation
│       ├── tool.call {tool, adapter, status}        (one per call, parallel visible)
│       ├── rag.retrieve {top_k, min_score}
│       └── llm.call ...
├── workflow.phase.awaiting_approval  {quorum, escalation_level}   ← long span, by design
├── workflow.phase.remediating
│   └── executor.gate / executor.action {action_type, dry_run} / executor.verify
└── workflow.phase.resolved
```

Because approval waits are spans, the flame graph of an incident *is* its MTTR breakdown —
"where did the 40 minutes go" is a picture, not a query. Async boundaries (bus hop, Temporal
scheduling) propagate context via metadata headers; Temporal interceptors (in
`aic_platform.temporal`) do this once for all workflows/activities.

## 21.2 Metrics: three families

**RED per service** (standard): `aic_http_requests_total{service,route,status}`, latency
histograms, in-flight gauges. **USE for the machinery**: Temporal task-queue depth + schedule-to-
start latency (the true worker saturation signal), stream lag per consumer group, DB pool
utilization, circuit-breaker states (`aic_adapter_circuit_state{adapter}`).

**Business/AI metrics** — the ones that make this an AI *platform*:

| Metric | Why it matters |
|---|---|
| `aic_incident_phase_duration_seconds{phase}` (histogram) | MTTR decomposition; NFR-2 SLOs alert on this |
| `aic_time_to_first_hypothesis_seconds` | the headline SLO (p95 < 3 min) |
| `aic_llm_tokens_total{model,agent_role,direction}` / `aic_llm_cost_usd_total{...}` | spend, attributable (NFR-7.1) |
| `aic_incident_budget_exhausted_total{phase}` | budget pressure — rising means budgets or agents need tuning |
| `aic_tool_calls_total{tool,status}` + latency | which integrations are slow/flaky |
| `aic_evidence_tainted_total` / `aic_gate_refusals_total` / `aic_redactions_total{channel}` | security signal (§17) |
| `aic_approvals_pending` (gauge) / `aic_approval_decision_seconds` | human-loop health — pending approvals piling up is an org problem surfaced by the platform |
| `aic_rca_accepted_ratio` (from §22 online signals) | is the AI actually helping |

## 21.3 Logs

structlog → JSON, every line carries `trace_id`, `incident_id`, `service`, `actor`. Levels are
semantic: `INFO` = state transitions and decisions, `WARNING` = degradation entered (circuit
open, budget 80%), `ERROR` = an SLO-relevant failure needing human attention. **No log-based
metrics** — if it's worth counting it's a Prometheus metric; logs are for reading during an
investigation, and log volume is bounded by sampling repetitive INFO in hot paths. Redaction
processor runs last (§20.4). Dev gets pretty console rendering; prod is machine-only.

## 21.4 Dashboards (checked into `deploy/grafana/`, provisioned, reviewed as code)

1. **Platform overview** — RED per service, queue depths, error budgets
2. **Incident flow** — funnel (triggered → triaged → RCA → proposed → approved → executed →
   verified), phase-duration heatmaps, active incidents by severity
3. **LLM operations** — cost/day by model + agent role, token histograms, latency, structured-
   output retry rates, budget-exhaustion counts
4. **Integrations** — per-adapter latency/error/circuit state
5. **Security** — taint flags, gate refusals, auth failures, redaction spikes

## 21.5 Alerting on AIC itself (who watches the watcher)

SLO burn-rate alerts (fast+slow windows) on: webhook ack latency, workflow-start lag,
time-to-first-hypothesis, API availability. Symptom alerts: task-queue schedule-to-start > 30 s,
stream lag growing 15 min, circuit open > 10 min, `aic_approvals_pending` age > policy timeout
(escalation misfiring), Postgres/Temporal/Redis health. Security alerts (§17.4) page the
platform team, not on-call app teams. Every alert maps to a runbook in `docs/runbooks/` — AIC
dogfoods its own runbook discipline. And the meta-rule from NFR-1.5: AIC alerting routes through
the org's existing pager, never through AIC.

## 21.6 Conventions that keep instrumentation from rotting

- Span/metric names and attributes are constants in `aic_platform.telemetry` — no string
  literals at call sites; renames are one-line diffs.
- New activity/tool/adapter instrumentation is inherited from base classes (§16.4) — you get
  spans/metrics/logs by construction; opting *out* is the code smell.
- Cardinality budget: labels are enums (service, phase, model, status), never IDs — incident
  IDs live in traces/logs (exemplars link metrics → traces where supported).
- The eval harness emits the same telemetry — an eval run is observably identical to
  production, which is what makes offline/online comparison honest (§22).
