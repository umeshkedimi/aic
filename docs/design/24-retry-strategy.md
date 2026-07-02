# 24. Retry Strategy

Retries are a layered responsibility: **each layer retries only the failures it can judge, and
every retry presumes the idempotency guarantees of §14.5.** Double-retrying (adapter retries
inside activity retries inside workflow logic) multiplies latency and load during exactly the
incidents when dependencies are already struggling — so ownership is exclusive per failure
class.

## 24.1 The ownership table

| Layer | Retries | Policy | Never retries |
|---|---|---|---|
| **Adapter (reads only)** | transient transport faults: connect reset, 502/503, DNS blips | 2 attempts, 100–400 ms jittered backoff, inside the call's timeout envelope | 4xx (semantic), 429 (defer to rate limiter), anything on **write** adapters — write retry semantics belong exclusively to Temporal + idempotency keys |
| **Tool pipeline** | nothing — converts failure to `ToolResult(status=error)` data (§16.3) | n/a | — the *agent* may choose an alternative source; that's routing, not retry |
| **Temporal activity** | activity-level failures: LLM 5xx/timeouts, DB unavailability, adapter exhaustion | exponential backoff 1 s → 2× → cap 60 s, max 5 attempts, full jitter | typed `NonRetryable` errors: schema-validation-final, policy refusal, gate refusal, auth failures, budget exhaustion |
| **Workflow logic** | *semantic* retries only: one re-proposal after rejection (§23.5), rollback after failed verification (§8.4) | explicitly bounded, policy-visible | technical failures (that's the activity layer's job) |
| **Structured-output loop** | LLM output that fails schema/semantic validation | ≤ 2 re-prompts with validation feedback (§13.4), *inside* one activity attempt | a third failure fails the activity attempt with `NonRetryable` — more model calls won't fix a model that can't produce the schema |
| **Bus consumer** | redelivery of unacked messages | consumer-group claim after visibility timeout; poison handling in §25.3 | — |
| **External senders** (Alertmanager, Datadog) | their own webhook retries on our 5xx | their policy; our contract is: 2xx only after persist (§8.1) | — |

## 24.2 Error taxonomy (one enum, used everywhere)

`aic_domain.shared.errors` defines the classification every layer maps into:

`TRANSIENT` (retry same target) · `RATE_LIMITED` (retry after — honor `Retry-After`, count
against the rate budget not the retry budget) · `UNAVAILABLE` (circuit-relevant; stop retrying,
route around) · `INVALID` (caller bug — never retry, surface loudly) · `FORBIDDEN`
(auth/policy — never retry, `SecurityEvent` where relevant) · `EXHAUSTED` (budget/quota — never
retry, degrade per §25).

Mapping is the adapter's job (§16.4); everything above adapters branches on the enum, not on
provider-specific exceptions. A new integration cannot introduce a new retry behavior — only a
new mapping.

## 24.3 Retry hygiene rules

1. **Full jitter everywhere** — synchronized retry waves during a shared-dependency outage are
   self-inflicted DDoS; jitter is not optional.
2. **Retry budgets over per-call heroics:** each incident phase has a wall-clock deadline
   (Temporal `schedule_to_close` per activity); retries fit inside it or the phase degrades.
   Better a partial investigation at minute 5 than a perfect one at minute 40.
3. **Circuit breakers cut retry storms at the adapter** (§16.4): open circuit converts retries
   into instant `UNAVAILABLE` — the backoff happens once (in the breaker's half-open probe),
   not per caller.
4. **Long activities heartbeat** (investigation, verification soak): Temporal detects a wedged
   worker in seconds via missed heartbeats instead of waiting out the full activity timeout,
   and `heartbeat details` carry resume cursors (verification remembers which probe iteration
   it was on).
5. **Retries are observable:** attempt number is a span attribute; `aic_retries_total{layer,
   error_class}` — a retry-rate spike is an early-warning signal *before* error budgets burn.

## 24.4 The LLM-specific case

LLM calls get special handling because failure modes are diverse: 429/5xx → activity retry with
backoff (and the concurrency governor already smooths bursts, §26.4); timeout → retry **once**
at same parameters, then degrade to the fallback model tier if configured (a slower-but-alive
answer beats none during an incident); malformed output → structured-output loop above;
content-filter refusals → `INVALID`, never retried (it will refuse again), logged for prompt
review. Every retry re-uses the *same* rendered prompt (no silent re-assembly — reproducibility),
except the structured-output loop which appends the validation feedback turn by design.
