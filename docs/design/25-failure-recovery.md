# 25. Failure Recovery

§24 covers retrying operations; this covers surviving *outages* — of AIC's own components, its
dependencies, and its data. Organizing principle: for every component, know (a) what degrades,
(b) what recovers automatically, (c) what a human must do, and (d) how we'd know.

## 25.1 Component failure matrix

| Failure | Degrades | Auto-recovery | Human action |
|---|---|---|---|
| `aic-api` pod(s) down | UI/API, approval intake | K8s reschedules; stateless; pending approvals wait durably in Temporal (nothing lost, decisions delayed) | none below SLO breach |
| `aic-ingest` down | webhook intake | sources retry on 5xx (their retry policy is part of our contract); on recovery, reconciler sweeps `alert_event` rows never published (persist-before-ack closes the crash window) | none |
| `aic-worker` crash mid-activity | in-flight reasoning step | Temporal times out the heartbeat, reschedules the activity on a healthy worker; completed evidence is already in Postgres (§15.2) — only the current step re-runs | none |
| `aic-executor` crash mid-action | in-flight action | activity retry hits the **idempotency key**: handler checks action state first (applied? verify post-condition and report; not applied? apply) — exactly the reason writes never retry blind (§24.1) | review `execution_record` if post-condition ambiguous |
| **Temporal** down | all workflow progress | ingest keeps accepting + persisting (start-workflow calls fail → reconciler backlog); API serves reads; on recovery, workflows resume from history, backlog drains | page platform on-call at 5 min; runbook: check backlog depth vs. capacity |
| **Postgres** down | everything (system of record) | HA failover (managed PG / Patroni), pods reconnect via pool; ingest returns 503 → sources buffer | page immediately; this is the one true hard dependency, treated accordingly (§25.4) |
| **Redis** down | dedup, rate limits, caches | per-family degradation (§11): dedup skips (duplicates > loss), rate limiting **fails closed for webhooks, open for authenticated reads**, caches miss to DB; streams: publishers fall back to reconciler-driven delivery | replace/restart; no data recovery needed — nothing durable lives there |
| **LLM provider** outage | intelligence plane | activity retries → fallback tier if configured (§24.4) → on exhaustion, incident escalates with partial evidence and a clear "AI unavailable" event; **alerting and paging are unaffected by design** (NFR-1.5) | consider switching provider config (a deploy-time config change, thanks to `LLMPort`) |
| Single integration outage | one evidence source / action target | circuit opens; investigations proceed with recorded gaps (FR-3.6); actions targeting it fail with typed errors → escalation | fix credentials/endpoint via `/admin/integrations` health surface |

## 25.2 Stuck-state detection (failures that don't crash)

The nastier class is silent wedging. Watchdogs, all driven off durable state (not process
liveness):

- **Workflow watchdog:** incidents in a non-terminal state with no event appended for >
  phase-SLO × 3 → alert with workflow ID (Temporal query shows exactly where it's stuck).
- **Approval-age alert** (§21.5) catches escalation-ladder misfires.
- **Reconciler** (ingest) is itself watched: `alert_event` rows unprocessed > 60 s is a paging
  alert — the safety net having holes must page.
- **Poison-message handling:** a message that kills its consumer N times goes to a
  dead-letter stream with full context; DLQ depth > 0 alerts; runbook covers inspect/fix/replay
  (replays are idempotent end-to-end, so replaying is always safe).

## 25.3 Manual intervention surface (the break-glass toolkit)

Deliberately small, all audited, all `admin`:

| Operation | Via | When |
|---|---|---|
| `POST /incidents/{id}/escalate` | API (operator) | take the AI out of the loop for one incident |
| Terminate + restart workflow | Temporal CLI runbook | corrupted workflow state after a bad deploy (with §14.3 versioning this should be ~never) |
| Replay DLQ message | runbook script | after fixing the poison cause |
| Re-run reconciler window | runbook script | suspected ingest gap |
| **Global pause switch** | `/admin/platform/pause` | halts new *executions* (approvals still collect; investigations continue read-only) — the "we don't trust it right now" lever, sub-second, reversible |

The global pause is the single most important operational control for an agentic platform:
adoption requires that the platform team can stop all write activity instantly without
un-deploying anything.

## 25.4 Data recovery

- **Postgres:** continuous WAL archiving + PITR; RPO ≤ 15 min / RTO ≤ 1 h (NFR-10.3); nightly
  logical backups for the schema-level "oops" class; quarterly restore drills (a backup that's
  never been restored is a hope, not a backup).
- **Temporal:** its persistence rides the same Postgres guarantees (self-hosted) or Temporal
  Cloud's SLA. Divergence case (PG restored to T-10min while Temporal is at T): workflows
  referencing missing rows fail their next activity → watchdog surfaces them → runbook
  reconciles (re-run or terminate); the append-only event spine makes "what's missing"
  computable.
- **RAG corpus:** embeddings are derivable (re-embed from documents) — only `knowledge.document`
  rows need recovery guarantees; a full re-embed is a batch job with a cost estimate, not a loss.
- **Redis/streams:** no recovery — by design nothing durable lives there; reconciler rebuilds
  delivery.

## 25.5 Disaster tiers and game days

DR posture is documented per deployment profile (this is OSS — operators choose): single-AZ dev
→ multi-AZ prod (HA Postgres, ≥ 2 replicas per service, PDBs) → multi-region as a §36 roadmap
item (active-passive, PG streaming replication; Temporal multi-cluster replication is the long
pole and is *not* hand-waved as easy).

Quarterly game days exercise the matrix above: kill a worker mid-investigation, drop Redis,
revoke an integration credential, restore last night's backup to a scratch cluster, flip the
global pause during a live (staged) incident. Each drill has a pass criterion tied to the NFR it
tests; failed drills open issues like failed tests do.
