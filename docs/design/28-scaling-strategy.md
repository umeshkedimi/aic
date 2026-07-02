# 28. Scaling Strategy

Scaling an agentic platform has an unusual shape: the compute-bound components are trivially
horizontal, while the true bottleneck (LLM throughput) doesn't scale with replicas at all. The
strategy is therefore: **make everything stateless-horizontal (done, §11), then manage the two
real constraints — LLM capacity and Postgres — explicitly.**

## 28.1 Per-component scaling model

| Component | Scales on | Mechanism | Notes |
|---|---|---|---|
| `aic-api` | CPU + p99 latency | HPA, 2→10 | read-heavy; cache-friendly |
| `aic-ingest` | req rate + stream lag | HPA, 2→10 | sized for 500/s burst (NFR-3.1); storm mode flattens downstream load, not intake |
| `aic-worker` | **task-queue depth / schedule-to-start latency** | KEDA (Temporal queue scaler), 2→20, night floor 2 | replicas raise *parallel incident* capacity; per-incident speed is LLM-bound |
| `aic-executor` | queue depth | fixed 2 → KEDA later | executions are rare/short; 2 replicas is HA, not capacity |
| Temporal | shard/persistence tuning | Temporal Cloud or self-host runbook | self-host: history shards sized at install (hard to change later — default 512) |
| Postgres | vertical first, then read path | see 28.3 | the deliberate single point of coordination |
| Redis | vertical → Cluster | keys are already hash-taggable per family | streams partition by incident ID if needed |

The worker-scaling subtlety worth stating: KEDA adding workers helps only while the LLM
governor (§26.4) has headroom. Past that, added replicas just park more coroutines in the
governor queue. The *governor's* queue-wait metric — not worker CPU — is the honest "we need
more LLM capacity" signal, and the response is a quota raise / second provider / regional
deployment decision, i.e. a human capacity-planning act, not an autoscaler's.

## 28.2 Concurrency within a worker

Workers are async end-to-end (one process, many concurrent activities): investigation
activities are ~99% await (LLM + tool I/O), so a single worker sustains ~50 concurrent
activities comfortably; Temporal's per-worker slot limits (`max_concurrent_activities`) are the
backpressure valve that keeps memory bounded. CPU-bound hotspots (embedding batches, large
digest parsing) run in thread executors so the event loop never starves heartbeats — a wedged
event loop looks like worker death to Temporal (§24.3) and gets work stolen, which is correct
but wasteful.

## 28.3 Postgres: the managed bottleneck

Ordered plan, each step deferred until its predecessor shows strain:

1. **Now (designed in):** partitioned event/llm-call tables (§9.3), covering + partial indexes
   for hot queries, async pool sizing per service, statement timeouts.
2. **Read path:** API read-model queries → read replica (the event spine is append-only, so
   replica lag = bounded staleness on timelines, acceptable and labeled in the UI). PgBouncer
   in front for connection multiplication across replicas.
3. **Write path:** the only high-rate writers are `alert_event`/`incident_event` appends and
   evidence inserts — batched inserts in ingest storm mode; `llmops` and raw-evidence payloads
   can move to their own database (schema boundaries are the cut lines, §9.1) before anything
   exotic is needed.
4. **pgvector:** HNSW query cost grows slowly; corpus 10M+ chunks or heavy multi-tenant QPS →
   lift `knowledge` schema to a dedicated vector DB behind `VectorStorePort` (NFR-9.2), which
   was the plan all along.

Explicitly rejected until proven necessary: sharding, CQRS with a separate event store,
distributed SQL. The 1M-incident/100M-event target (NFR-3.4) fits comfortably in one
well-partitioned Postgres.

## 28.4 The 10× / 100× narrative (what breaks first, in order)

**At ~10× (1k incidents/day, thousands of alerts/min):** LLM quota is the first wall →
second provider via `LLMPort` + governor pools per provider (also a resilience win). Then
Redis Streams consumer fan-out limits → swap `EventBusPort` adapter to Kafka (the designed
exit, NFR-9.3). Grafana/Prometheus fine; Temporal self-host needs its Postgres sized up or
moved to Temporal Cloud.

**At ~100× (multi-org SaaS shape):** tenancy becomes the architecture (per-tenant namespaces
in Temporal, row-level tenancy in PG or DB-per-tenant, per-tenant budgets/quotas everywhere) —
a §36 roadmap item with its own design round, not an extrapolation of this document. The
honest statement: the current design is a *single-org platform* that scales to a large org's
full incident volume; multi-tenancy is a product decision, not a scaling patch.

## 28.5 Load and soak validation

Capacity numbers above are claims until tested: the load suite (`tests/load/`, Locust or k6)
replays a recorded alert-storm profile at 1×/5×/10× against a staging stack with fake adapters
and a stubbed LLM (latency-realistic), asserting the NFR-2 latencies and zero alert loss.
Soak: 24 h at nominal load watching for pool exhaustion, memory creep in workers (LangGraph
state leaks are a known genre), partition-pruning regressions, and Temporal history growth
rate. Runs before any release that touches ingest, workflow shape, or dependencies — and the
profile itself is versioned with the scenarios (§22.2).
