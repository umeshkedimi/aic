# 27. Cost Optimization

Cost discipline for an agentic platform is architecture, not accounting: the big savings were
designed in sections 13–16; this document consolidates the levers, the spend model, and the
governance loop. Guiding constraint: **optimize cost per *resolved incident*, never cost per
LLM call** — a $0.40 investigation that misses the root cause costs an hour of engineers.

## 27.1 Where the money goes (unit economics, standard-depth incident)

| Component | Est. cost | Driver |
|---|---|---|
| Triage (cheap tier, 1 call) | ~$0.01 | small context, small output |
| Evidence digestion (cheap tier, ~10 calls) | ~$0.05 | raw tool output volume |
| RCA reasoning (frontier, 2–4 calls) | ~$0.60–1.00 | packed context (§15.4) × frontier pricing |
| Proposal + self-check (frontier, ≤3 calls) | ~$0.20 | moderate context |
| Scribe (cheap tier) | ~$0.03 | event-log summarization |
| Embeddings (queries + indexing amortized) | ~$0.01 | tiny at text-embedding-3-small pricing |
| **Total, default budget $2.00 cap** | **~$0.90–1.30 typical** | headroom for iteration loops |

At 500 incidents/month: ~$500–650 LLM spend — noise against one engineer-hour saved per
incident. The model matters anyway, because *unmanaged* agentic cost curves are superlinear:
one unbounded loop, one raw-payload context, one retry storm and the bill is 50×. Every lever
below exists to keep the distribution's tail short, not to shave the median.

## 27.2 The levers, ranked by effect size

1. **Tiered model routing** (§13.2): the frontier model touches only RCA synthesis and
   proposals. ~80% of calls run on the cheap tier; this single decision is worth more than all
   others combined.
2. **Digest-before-reason** (§13.3): frontier context is built from compressed digests, never
   raw payloads — cuts frontier input tokens ~5–10× and *improves* signal density.
3. **Hard budgets with graceful degradation** (NFR-7.2): per-incident token/cost/tool-call caps
   enforced by the budget governor; exhaustion is a normal exit with partial results. Depth
   tiers (quick/standard/deep) set at triage — a flapping SEV-4 gets quick depth (~$0.15 cap),
   not the full treatment.
4. **Storm batching** (§26.2): alert storms cost O(incidents), not O(alerts) — the tail-risk
   cap for the scariest organic scenario.
5. **Provider prompt caching**: system prompts and stable context prefixes are ordered
   cache-friendly (static → semi-static → volatile) so provider-side prefix caching discounts
   apply; the packer's deterministic layout (§15.4) makes hit rates measurable and non-accidental.
6. **Embedding cache + content hashing** (§15.6, §9.3): never embed the same text twice.
7. **Eval spend control** (§22): PR gates run the affected suite, not everything; nightly runs
   are full; judge calls are the pinned cheap-capable model; eval traffic is lowest priority in
   the governor queue (§26.4).

Consciously deferred: semantic response caching (incident contexts rarely repeat; staleness
risk > savings — revisit with hit-rate data), self-hosted models (operational cost swamps
API savings at this scale; the `LLMPort` keeps the door open), batch APIs for the Scribe
(latency-tolerant, 50% discount — good §36 item once volume justifies plumbing).

## 27.3 Attribution and governance

Every dollar is attributable three ways (already built: `llmops.llm_call` §9.3, cost metrics
§21.2): **per incident** (shown on the incident page — humans should see what an investigation
cost), **per agent role × model** (which prompt changes moved spend — joined with eval scores,
§22.5's cost gate), **per depth tier** (are budgets sized right — budget-exhaustion rate by
tier is the tuning signal).

Governance loop: default budgets in config → monthly review of the exhaustion-rate and
tail-spend dashboards → global monthly budget alarm at 80% (NFR-7.4) → the global pause switch
(§25.3) as the emergency spend brake (it also halts LLM-burning investigations if a bug melts
money — pausing executions pauses new phase work).

## 27.4 Infrastructure cost posture (the smaller half)

Deliberate cheapness is already structural: pgvector instead of a managed vector DB (one
Postgres bill), Redis Streams instead of Kafka (no broker fleet), modular monolith instead of
microservice sprawl (4 deployments), scale-to-few (executor at 2 replicas; workers scale on
queue depth and *down* to a floor at night — Temporal tolerates worker absence, work just
queues). The upgrade paths (§28) are paid for when the scale that justifies them arrives, not
before. Observability stack is the usual self-host vs. SaaS tradeoff — documented as operator
choice, with self-host defaults in the compose/k8s manifests so the OSS project is
zero-license-cost by default.
