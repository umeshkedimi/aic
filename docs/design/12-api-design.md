# 12. API Design

## 12.1 Conventions (apply to every endpoint)

| Concern | Convention |
|---|---|
| Base path | `/api/v1` on `aic-api`; `/webhooks/*` on `aic-ingest` (separate ingress — different auth model, different exposure) |
| Versioning | URL-versioned. Within v1: additive changes only (new fields/endpoints); breaking change → `/api/v2`. OpenAPI snapshot diffed in CI to catch accidental breaks |
| Auth | `Authorization: Bearer <JWT>` (humans, via OIDC exchange) or `X-API-Key` (machines, scoped). Webhooks: per-source HMAC signature headers |
| Errors | RFC 9457 `application/problem+json`, one envelope everywhere (below) |
| Pagination | Cursor-based: `?limit=50&cursor=<opaque>` → `meta.next_cursor`. Offset pagination rejected: unstable under concurrent inserts and O(n) at depth |
| Filtering | Explicit query params (`status`, `service`, `severity`, `since`, `until`) — no generic query language in v1 |
| Idempotency | Mutating POSTs accept `Idempotency-Key`; replays return the original result (24 h window, Redis) |
| IDs | UUIDv7, opaque to clients |
| Mutations | Domain operations, not raw field patches: `POST /incidents/{id}/close`, never `PATCH {"status": "closed"}` — the state machine stays in the domain layer |
| Rate limits | Per-principal token bucket; `429` + `Retry-After`; standard `RateLimit-*` headers |
| Correlation | `X-Request-ID` honored or generated, echoed in responses and logs |

**Error envelope:**

```json
{
  "type": "https://aic.dev/errors/policy-forbidden",
  "title": "Action forbidden by policy",
  "status": 403,
  "detail": "DeleteResource is forbidden in all environments by rule pr_9f2c (v3).",
  "instance": "/api/v1/approvals/0198c2f1-.../decision",
  "request_id": "req_01HN...",
  "errors": []
}
```

`errors[]` carries field-level validation details on 422s (Pydantic-derived).

## 12.2 Resource map

### Incidents

| Method + Path | Purpose | Role |
|---|---|---|
| `GET /incidents` | List/filter (status, service, severity, time range; cursor) | viewer |
| `POST /incidents` | Manual incident (FR-1.6); enters the same workflow | operator |
| `GET /incidents/{id}` | Full incident: current state, severity, links, phase durations | viewer |
| `GET /incidents/{id}/timeline` | The event log, paginated — the audit view | viewer |
| `GET /incidents/{id}/evidence` | Evidence items (digests; `?full=true` for raw where retained) | viewer |
| `GET /incidents/{id}/rca` | RCA: ranked hypotheses with evidence citations | viewer |
| `GET /incidents/{id}/proposals` | Proposals + per-action policy decisions and statuses | viewer |
| `POST /incidents/{id}/escalate` | Human takeover; agent stops proposing | operator |
| `POST /incidents/{id}/resolve` | Manual resolve (reason required) | operator |
| `POST /incidents/{id}/close` | Post-review close | operator |
| `POST /incidents/{id}/reclassify` | Severity override (recorded with actor) | operator |
| `GET /incidents/{id}/export?format=json\|markdown` | Compliance export (FR-8.3) | viewer |

### Approvals

| Method + Path | Purpose | Role |
|---|---|---|
| `GET /approvals?status=pending` | Approval inbox (the web counterpart to Slack cards) | approver |
| `GET /approvals/{id}` | Full context: action, params, dry-run, evidence summary, blast radius, policy rule | approver |
| `POST /approvals/{id}/decision` | `{"decision": "approve" \| "reject", "reason": "..."}` — RBAC + quorum enforced; forwarded as Temporal signal | approver |

Decisions are immutable; a second decision by the same principal on the same request → `409`.

### Knowledge

| Method + Path | Purpose | Role |
|---|---|---|
| `POST /knowledge/documents` | Ingest (markdown + metadata) → chunk/embed pipeline; returns `202` + document id | operator |
| `GET /knowledge/documents` / `{id}` | List/inspect (versions, chunk counts, active flag) | viewer |
| `DELETE /knowledge/documents/{id}` | Deactivate (soft; excluded from retrieval) | operator |
| `POST /knowledge/search` | `{"query": "...", "filters": {"service": "..."}}` → chunks with scores (debugging/UI; the same retrieval the agent uses) | viewer |

### Policies & Admin

| Method + Path | Purpose | Role |
|---|---|---|
| `GET /policies` | Active rule set (action × environment matrix view) | viewer |
| `POST /policies` | Create rule **version** (immutable versioning per §9.3) | admin |
| `POST /policies/simulate` | Dry-run a hypothetical action against current policy — "what would happen if" | operator |
| `GET /policies/{id}/versions` | Version history with authors | viewer |
| `GET/POST /admin/integrations` | Integration configs + health status (creds via secret refs, never in payloads) | admin |
| `GET/POST /admin/alert-sources` | Webhook source registration → issues per-source secret | admin |
| `GET/POST /admin/users`, `/admin/role-assignments` | RBAC management | admin |
| `POST /admin/api-keys` | Scoped machine keys (hash stored; secret shown once) | admin |
| `GET /audit` | Cross-incident audit query (actor, action type, time range) | admin |

### Auth & Meta

| Method + Path | Purpose |
|---|---|
| `POST /auth/token` | OIDC code exchange → JWT (+ refresh) |
| `GET /auth/me` | Current principal, roles, effective permissions |
| `GET /healthz`, `GET /readyz` | Liveness (process up) vs readiness (deps reachable) — on **every** service |
| `GET /metrics` | Prometheus exposition — every service, cluster-internal only |

### Webhooks (`aic-ingest`, separate ingress)

| Method + Path | Auth |
|---|---|
| `POST /webhooks/alertmanager` | HMAC shared secret + timestamp window |
| `POST /webhooks/datadog` | Per-source secret header |
| `POST /webhooks/cloudwatch` | SNS signature verification (incl. subscription confirm handshake) |
| `POST /webhooks/slack/interactions` | Slack signing secret (lives on `aic-api`, listed for completeness) |

## 12.3 Representative payloads

**`GET /incidents/{id}`** (trimmed):

```json
{
  "id": "0198c2f1-7c3a-7d21-a5f2-3e9b1c2d4e5f",
  "title": "PaymentServiceP99LatencyHigh",
  "status": "awaiting_approval",
  "severity": "sev2",
  "service": "payment-service",
  "environment": "prod",
  "source": "alertmanager",
  "links": { "slack_channel": "C08...", "jira_issue": "OPS-2041" },
  "phase_durations_ms": { "triaging": 41000, "investigating": 96000 },
  "rca_summary": {
    "top_hypothesis": "v2.14.1 pool-size reduction causing connection exhaustion",
    "confidence": 0.85,
    "hypothesis_count": 3
  },
  "pending_approvals": 1,
  "created_at": "2026-07-02T03:12:04Z",
  "updated_at": "2026-07-02T03:15:21Z"
}
```

**`POST /approvals/{id}/decision`:**

```json
{ "decision": "approve", "reason": "Evidence is conclusive; rollback is the standard fix." }
```

→ `200` with the updated approval state (quorum progress), or `403` (missing role), `409`
(already decided / expired), `422` (reject without reason).

## 12.4 Design notes worth defending

1. **Approvals are first-class resources**, not a field on incidents — they have their own
   lifecycle, permissions, and audit trail, and Slack/web are just two views of the same record.
2. **`policies/simulate` exists because policy bugs are security bugs.** Admins get a test
   surface before a 3 a.m. incident exercises a bad rule.
3. **Read-heavy shape is deliberate.** Humans mostly *watch* AIC and decide at gates; the write
   surface is small, verb-explicit, and therefore easy to audit and rate-limit strictly.
4. **The OpenAPI document is generated from code but contract-tested in CI** — the spec is an
   artifact, not documentation drift.
5. **SSE stream for live incident timelines** (`GET /incidents/{id}/timeline/stream`) is
   deferred to the roadmap (§36) — polling the timeline is adequate for MVP and SSE adds ingress
   complexity we don't need yet.
