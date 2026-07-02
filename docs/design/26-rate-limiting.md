# 26. Rate Limiting

Four distinct surfaces get rate-limited, for four distinct reasons. Conflating them (one
"rate limiter" setting) is how systems end up throttling the wrong thing during an alert storm.

| Surface | Protects | Mechanism |
|---|---|---|
| Inbound API | AIC from clients | token bucket per principal |
| Inbound webhooks | AIC from alert storms & hostile senders | token bucket per source + storm mode |
| Outbound integration calls | **customer systems from AIC** | token bucket per `rate_key` |
| LLM provider calls | provider quota + spend | concurrency governor + priority queue |

All buckets are Redis-backed (atomic Lua for take-or-reject), keyed hierarchically
(`rl:{surface}:{key}`), with limits in config (per-deployment tuning, not code).

## 26.1 Inbound API

Per-principal token bucket (defaults: 600 req/min human, 300 req/min API key; mutation routes
lower). Standard `RateLimit-*` headers, `429` + `Retry-After` (§12.1). Fail-open on Redis loss
for authenticated **reads** with an alert (availability > strictness for humans watching
incidents), fail-closed for mutations. Admin routes additionally get small burst caps —
nobody legitimately creates 50 policies a second (T10 hygiene).

## 26.2 Webhooks: absorb storms, reject floods

Two regimes on the same endpoint, because UC-3 (organic storm) and T8 (hostile flood) look
similar at the socket but need opposite treatment:

- **Per-source bucket sized generously** (default 100/s per source — a real storm from a real
  Alertmanager must land; alerts that reach us get deduped/correlated *after* acceptance, which
  is the cheap path).
- **Storm mode** (trips at sustained > 50% bucket usage): ingestion continues, but correlation
  aggressively batches — new alerts matching an open storm-incident's fingerprint family attach
  without per-alert LLM work, and triage for the storm incident runs once per batch window
  (30 s), not per alert. Storm mode is an *ingest optimization*, invisible to durability
  guarantees.
- **Unregistered/failed-auth senders**: tiny IP-level bucket (protects signature verification
  CPU), `SecurityEvent` on repeat offenders (§18.3).

## 26.3 Outbound: AIC as a polite citizen

The limiter most platforms forget. Every `ToolSpec`/handler carries a `rate_key` (§16.1)
scoping a bucket per **target system**, with defaults set well below typical service capacity
(e.g. `prometheus: 10/s`, `github: 2/s` — GitHub's secondary limits are real, `k8s-api: 20/s`).
Rationale: fifty concurrent investigations fanning out tool calls must not become a thundering
herd against the customer's Prometheus — *AIC degrading the systems it's investigating is the
most embarrassing failure mode this product can have.* Blocked callers wait (bounded by the
tool timeout) rather than error, so the agent experiences slowness, not failure. Bucket
saturation is a metric (`aic_rate_wait_seconds{rate_key}`) — sustained waits mean raise the
limit consciously, with the customer, not implicitly by bug.

## 26.4 LLM: a governor with a priority queue, not a limiter

LLM capacity is different: it's the shared, expensive, quota-bound resource that *all*
intelligence competes for, and during a big outage demand spikes exactly when it matters most.
So instead of reject-on-limit:

- **Concurrency governor** per provider+model (e.g. 8 concurrent frontier calls, 32 cheap-tier)
  sized against provider rate limits with headroom; token-throughput tracking against TPM
  quotas.
- **Priority queue in front of it:** `SEV-1 > SEV-2 > … > Scribe/documentation > eval runs`.
  Priority is taken from incident severity — the platform's own understanding of what matters
  funds its resource arbitration. Aging prevents starvation of low tiers (a SEV-4's triage
  eventually runs), and per-tier queue-depth metrics make contention visible.
- Provider `429`s still occur (shared org quotas): honored via `Retry-After` and fed back as a
  governor concurrency reduction (multiplicative decrease, slow additive recovery) — the
  governor *learns* the real ceiling instead of hammering it.
- Interplay with money: the governor controls *throughput*; budgets (§27) control *spend*.
  A call must clear both — plenty of quota doesn't excuse blowing the incident's budget, and
  budget remaining doesn't excuse exceeding provider limits.

## 26.5 Interactions worth stating

- Rate-limited outbound calls do **not** consume activity retry budgets (§24.2:
  `RATE_LIMITED` ≠ `TRANSIENT`) — waiting in a bucket is normal operation, not failure.
- Storm mode + the governor together bound worst-case LLM spend of an alert storm to
  O(storm incidents × batch windows), not O(alerts) — the cost-safety argument for UC-3,
  quantified in §27.
- Every limiter emits the same metric family (`aic_rate_limited_total{surface,key}`,
  wait-time histograms) so "who is being throttled and why" is one dashboard panel (§21.4).
