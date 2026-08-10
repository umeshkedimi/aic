# ADR 0002: Kafka for alert-event propagation

## Status
Accepted (2026-08-10)

## Context
`aic-ingest` receives Alertmanager webhooks and must hand each normalized `AlertEvent` to a
correlator that groups related alerts into incidents. At the signature scenario's scale this is
one producer and one consumer — a direct function call or Postgres `LISTEN`/`NOTIFY` would be
strictly simpler and would satisfy every functional requirement of this milestone.

The project mandate lists Kafka/event-driven processing explicitly among the things the author
must be able to explain and defend, and treats engineering depth on the chosen stack as more
valuable than avoiding a component that isn't strictly load-bearing yet.

## Decision
Introduce one real Kafka topic, `alert-events`, between `aic-ingest` (producer) and the correlator
(consumer group `aic-correlator`), even though a single consumer doesn't strictly require it. This
is a deliberate scope choice to get genuine, defensible Kafka engineering — partitioning strategy,
consumer group semantics, at-least-once delivery, idempotent consumption — into the one path that
matters, rather than bolting Kafka onto a system later without it ever being exercised for real.

Partition key = incident fingerprint (service + correlation window), so alerts destined for the
same incident are strictly ordered within a partition — this matters the moment there's more than
one partition or consumer instance, which there will be.

## Consequences
- `aic-ingest` needs a Kafka producer with `acks=all` and idempotent producer settings; a lost or
  duplicated `AlertEvent` is worse than a slow one.
- The correlator must be idempotent on `(fingerprint, alert_fingerprint)` regardless of Kafka's
  at-least-once delivery — consumer-side dedup via a Postgres unique constraint, not "Kafka
  handles it."
- Local dev requires a running broker (Redpanda or Kafka via Docker/kind), which is one more
  thing to operate. Accepted as the cost of the depth this buys.
- `EventBusPort` still wraps the producer/consumer so a future swap (e.g. to a managed service)
  is a config change, not a rewrite — abstraction isn't abandoned just because the concrete choice
  came pre-decided.
