# 16. Tool Architecture

Tools are where the probabilistic system touches the real world — so the tool layer is where
most of the engineering rigor concentrates. A "tool" in AIC is a typed capability with a
declared risk class, not a function the LLM happens to be able to call.

## 16.1 Anatomy of a tool

Every tool is defined by a `ToolSpec`:

| Field | Meaning |
|---|---|
| `name`, `description` | LLM-facing; description is versioned prompt material |
| `input_schema` / `output_schema` | Pydantic models — validated on the way in *and* out |
| `capability` | `READ` or `WRITE` — structural, not advisory (see 16.2) |
| `adapter` | which integration adapter fulfills it |
| `timeout` | per-call, always set (no unbounded awaits) |
| `cost_class` | `free / metered / expensive` — the budget governor weighs calls |
| `rate_key` | rate-limit bucket (protects the *target* system — a runaway agent must not DoS the customer's Prometheus) |
| `result_digest_policy` | how raw output is compressed to Evidence (max tokens, digest prompt or deterministic summarizer) |

MVP read-tool catalog (bound to the investigation agent):

`k8s.get_pod_status`, `k8s.get_recent_events`, `k8s.get_deployment_history`,
`prometheus.instant_query`, `prometheus.range_query`, `github.list_recent_deploys`,
`github.get_pr_diff`, `knowledge.search` (RAG as a tool — the agent decides when to recall).

## 16.2 The READ/WRITE split is structural

- **READ tools** are LLM-callable. They live in `aic_integrations` behind the `[readonly]`
  extra and are the only tools the agent graphs can bind (§7.1 rule 4 — enforced at import and
  packaging level).
- **WRITE capabilities are not tools at all.** They are `ActionHandler`s in `aic-executor`,
  reachable only via the proposal → policy → approval → execution pipeline. There is no code
  path in which an LLM output string selects and invokes a write adapter directly. The agent's
  relationship to writes is *referential*: it proposes catalog entries by type + typed params.

This asymmetry is the platform's core safety property, worth restating as a test: grep the
worker's installed packages for a Kubernetes write call — it isn't there.

## 16.3 Execution pipeline for a read tool call

```
agent selects tool + args
  → input schema validation (reject malformed before spending I/O)
  → budget governor check (calls remaining? cost class affordable?)
  → rate limiter (per rate_key, Redis token bucket)
  → circuit breaker (per adapter; open → typed ToolUnavailable, no I/O)
  → adapter call with timeout (asyncio.timeout)
  → output schema validation
  → redaction filter
  → digest (per result_digest_policy)
  → Evidence row persisted (query, digest, latency, status)
  → digest returned to the graph
```

Failures surface to the agent as **data, not exceptions**: a `ToolResult` with
`status: error, error_class: timeout | unavailable | forbidden | invalid_args` and a short,
injection-safe message. The agent can route around a dead source (try Prometheus when Datadog
is down); the graph never crashes because a dependency blinked. Every failure is still an
Evidence row — "we couldn't see X" is itself investigative signal, and it's how the RCA can
honestly report gaps.

## 16.4 Adapter base: one implementation of the boring-but-critical parts

All adapters extend `AdapterBase`, which owns: connection/session lifecycle, timeout
enforcement, retry policy *for idempotent reads only* (writes never auto-retry at the adapter
layer — retry semantics for actions belong to Temporal + idempotency keys), circuit breaker
(rolling failure window → open → half-open probes), health check (surfaced in
`/admin/integrations` and readiness), OTel span per call, Prometheus metrics
(`aic_tool_calls_total{tool,status}`, latency histograms), and typed error taxonomy mapping
(HTTP 429 → `RateLimited`, 401/403 → `AuthFailed` — which pages *platform* operators, not the
incident flow).

Writing a new adapter is therefore mostly writing the happy path — the operational envelope is
inherited. That's what makes NFR-9.4 ("new integration ≤ 2 days") realistic.

## 16.5 Prompt-injection defense at the tool boundary

Tool output is **untrusted input** — logs, pod annotations, GitHub PR descriptions are all
attacker-writable in a real org. Layered controls:

1. **Structural:** the worst an injected instruction can achieve is influencing *proposals* —
   which still face catalog typing, policy, and human approval. The execution plane never sees
   LLM text (§11.4).
2. **Digest firewall:** raw output passes through the digesting step whose prompt treats
   content strictly as data to summarize; digests are delimited and tagged as
   `untrusted-source` material in downstream prompts.
3. **No echo privilege:** tool results never contribute to *system* prompts, only to clearly
   fenced context sections.
4. **Detection:** digest step flags instruction-like patterns ("ignore previous…") →
   `SecurityEvent` + evidence marked tainted; tainted evidence is highlighted in approval cards
   so the human gate sees it.

## 16.6 Adding a tool: the checklist as API

New tool = PR containing: `ToolSpec` + schemas, adapter method (or new adapter extending
`AdapterBase`), digest policy, rate key + limits for the target system, contract tests against a
recorded fixture (§32), registry entry binding it to specific agent roles, and an eval-set spot
check that the agent uses it sensibly. No core changes; the registry is additive (NFR-9.5's
tool-side counterpart).

## 16.7 Action handlers (the write side, for symmetry)

Handlers mirror ToolSpec discipline with three additions: **dry-run** implementation per type
(`kubectl rollout` equivalents in plan mode, attached to approval cards), **rollback pairing**
(a handler declares its inverse where one exists: `ScaleDeployment` ↔ restore previous replica
count; `SilenceAlert` ↔ expire silence), and **post-conditions** (a typed check that the action
took effect — distinct from incident-level verification, which asks whether it *helped*).
Handlers are registered against catalog entries 1:1; an approved action with no registered
handler is a deployment error caught at executor boot, not at 3 a.m.
