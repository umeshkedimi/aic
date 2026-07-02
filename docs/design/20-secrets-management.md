# 20. Secrets Management

## 20.1 Inventory first

You cannot manage secrets you haven't enumerated. AIC's complete secret inventory, by
consumer:

| Secret | Consumed by | Blast radius if leaked |
|---|---|---|
| Integration write credentials (K8s SA tokens, Jira/Slack write tokens) | executor only | **Critical** — infrastructure mutation |
| Integration read credentials (K8s RO token, Prometheus/GitHub read) | worker only | High — recon over customer systems |
| LLM API keys | worker only | Medium — spend + data egress channel |
| DB credentials (per-service roles) | each service, own role only | High, but grants cap damage (§9.4) |
| JWT signing key (ES256 private) | api only | Critical — mint any identity |
| Webhook per-source HMAC secrets | ingest (verify), api (issue) | Medium — forge alerts (T2) |
| Slack signing secret + bot token | api | Medium — forge approvals attempts (T3) |
| OIDC client secret | api | Medium |
| Temporal mTLS certs | all services, per-service certs | High — task queue access (T6) |
| Redis ACL passwords | each service | Low–medium |

The table *is* the policy: each row names exactly one or two consumers, and that is enforced —
no secret is mounted into a pod whose row doesn't name it. The worker cannot leak write
credentials because the worker never possesses them (T4).

## 20.2 Sourcing: External Secrets Operator, secret-manager-backed

- **Production/staging (K8s):** secrets live in the cloud secret manager (AWS Secrets Manager /
  GCP Secret Manager / Vault). **External Secrets Operator (ESO)** syncs them into namespaced
  K8s `Secret`s consumed as env vars via `envFrom`. AIC pods never talk to the secret manager
  directly — ESO centralizes the IAM story, and rotation becomes "update the manager, ESO
  re-syncs, pods restart on secret change (Reloader annotation)."
- **Local dev:** `.env` file (gitignored), `​.env.example` documents every variable with a
  placeholder — a new engineer sees the full inventory without seeing a single value.
- **CI:** GitHub Actions environments + OIDC federation to the cloud (no long-lived cloud keys
  in GitHub); test runs use throwaway containerized deps with generated credentials.

`pydantic-settings` loads and validates at boot: a service missing a required secret **fails
fast at startup** with a named field error — never a mid-incident `KeyError` at 3 a.m.
Validation includes shape checks (key prefixes, PEM headers) to catch "right var, wrong value"
paste accidents.

## 20.3 Integration credentials: references, not values

`/admin/integrations` configs (§12.2) store **secret references** (`secretRef:
aws-sm://aic/prod/github-read`), never values. The API writes the reference to Postgres; only
the consuming service resolves it (via its mounted ESO-synced secret). Consequences: the DB
dump contains no credentials, the admin API never round-trips a secret, and credential rotation
touches zero AIC config.

## 20.4 Keeping secrets out of the three leak channels

1. **Logs:** structlog processor pipeline includes a redaction processor (field-name
   denylist + value-pattern scan: key prefixes `sk-`, `xoxb-`, `ghp_`, AWS key shapes, PEM
   blocks, high-entropy strings ≥ threshold). Runs last, after all enrichment. Redactions emit
   a metric (`aic_redactions_total{channel="log"}`) — a spike means someone's code is trying
   to log secrets.
2. **Prompts/evidence:** the two-pass redaction from §15.4 (at persistence, at context
   assembly) uses the same shared detector from `aic_platform.security.redaction` — one
   implementation, three call sites, one test suite.
3. **Repo/images:** gitleaks in CI (and as pre-commit hook); image scanning includes
   secret detection; `uv.lock` review for typosquats. `.env` and `*.pem` in `.gitignore` from
   day one.

## 20.5 Rotation and revocation

| Secret class | Rotation | Mechanism |
|---|---|---|
| JWT signing key | 90 days, overlap window | JWKS serves old + new `kid` during overlap; access tokens are 15 min so drain is fast |
| API keys | ≤ 90 days enforced expiry | stale-key report; creation-time expiry mandatory (§18.2) |
| Webhook HMAC secrets | on demand + annual | dual-secret verification window (old+new accepted for 24 h) so sources cut over without dropped alerts |
| Integration credentials | provider-driven | reference indirection (§20.3) makes this AIC-invisible |
| DB/Redis/Temporal certs | 90 days | ESO re-sync + rolling restart |

Revocation drill (leaked-key runbook, `docs/runbooks/`): revoke at source → ESO sync → rolling
restart ≤ 5 min → audit-log sweep of the key's `last_used` window. Practiced, not theoretical —
it's a game-day exercise in §35's checklist.

## 20.6 Temporal payloads: the secret channel people forget

Workflow histories persist activity inputs/outputs for the retention period — evidence snippets
and incident data would otherwise sit plaintext in Temporal's DB, a second copy outside our
grant model. A **payload codec** (Temporal's converter interface) encrypts payload fields with
a data key (AES-GCM, key from the secret manager, `key_id` in the payload metadata for
rotation). Temporal's own operators see ciphertext; only AIC workers holding the key decode.
This also covers the Temporal Web UI: staff browsing workflow histories see redacted payloads
unless explicitly authorized via the codec-server endpoint.
