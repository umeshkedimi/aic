# 18. Authentication

Four kinds of principals authenticate to AIC, each with a different mechanism chosen for its
threat profile. No principal type shares another's mechanism.

| Principal | Mechanism | Lifetime |
|---|---|---|
| Humans (web/CLI) | OIDC (enterprise IdP) → AIC-issued JWT | access 15 min / refresh 8 h |
| Machines (CI, scripts, portals) | Scoped API keys | ≤ 90 days, rotated |
| Alert sources (webhooks) | Per-source HMAC signatures | secret rotated per source |
| Slack (interaction callbacks) | Slack signing secret + identity binding | per Slack app config |

## 18.1 Humans: OIDC against the enterprise IdP

AIC **never stores passwords** and never will — enterprises have Okta/Entra/Auth0, and any
credential store we build is pure liability. Flow: Authorization Code + PKCE against the
configured IdP → `POST /auth/token` exchanges the IdP token → AIC issues its own JWT.

Why AIC issues its own tokens instead of passing IdP tokens through: (1) AIC-local claims
(roles resolved from `iam.role_assignment`) belong in the token without writing to the customer
IdP; (2) IdP migration doesn't invalidate the API contract; (3) token lifetime policy is ours.

**JWT contents** (signed ES256, keys rotated, `kid` header, JWKS endpoint for verification):

```json
{
  "iss": "aic", "sub": "usr_0198...", "exp": 1751500000, "iat": 1751499100,
  "sid": "ses_0198...",
  "roles": ["operator", "approver"],
  "idp_sub": "okta|00u8...",
  "token_use": "access"
}
```

- **15-minute access tokens** make revocation latency acceptable without a token blocklist;
  refresh tokens are opaque, stored hashed, one-time-use (rotation on refresh — replay of a
  used refresh token kills the whole session family).
- Role changes take effect at next refresh (≤ 15 min) — acceptable for grants; *revocations*
  and user disablement are also checked against the DB on the **approval-decision and admin
  endpoints specifically** (the writes that matter don't wait out a token TTL).

## 18.2 Machines: API keys done properly

Format `aic_<key_id>_<secret>`; server stores `key_id → argon2id(secret), scopes, expiry,
last_used`. Shown once at creation. Constraints: mandatory expiry (≤ 90 days), mandatory scope
list (§19.4) — an unscoped key cannot be created, there is no "god key" shape. Verification is
a hash comparison + scope check; no JWT machinery. `last_used` powers a stale-key report in
`/admin/api-keys`.

## 18.3 Webhook sources: HMAC with replay protection

Registering an alert source (`POST /admin/alert-sources`) issues a per-source secret. Senders
sign: `X-AIC-Signature: v1=HMAC_SHA256(secret, timestamp + "." + body)` plus
`X-AIC-Timestamp`. Verification: constant-time compare, timestamp within ±5 min (replay
window), fingerprint dedup absorbs the remainder. Sources that can't add headers (CloudWatch
SNS) use their native verification (SNS message signature validation) — the adapter per source
owns its verification strategy. A failed verification is a `SecurityEvent`, rate-limited per
source IP.

## 18.4 Slack: two-step identity

Slack interaction callbacks are verified with the Slack signing secret (v0 scheme, timestamp
window). But *Slack authenticity ≠ approver identity*: the Slack user ID is then resolved
through an explicit binding table (`iam.user_account.slack_user_id`, admin-managed or
email-matched at first use with confirmation). An unbound Slack user tapping "Approve" gets a
polite ephemeral refusal and the tap is audit-logged. Approval authority never derives from
Slack workspace membership.

## 18.5 Service-to-service: mostly by construction

There are no internal HTTP APIs (§11.6), which deletes the internal token matrix most
platforms maintain. What remains:

- **Temporal:** mTLS client certs per service; namespace-scoped access; workers can only poll
  their own task queues.
- **Postgres:** per-service roles with SCRAM auth + TLS; grants are the authorization (§9.4).
- **Redis:** ACL users per service (ingest can XADD, api can only read its cache keys, etc.).

## 18.6 Enforcement placement

One FastAPI dependency chain (`aic_platform.security`) used by every router:
`authenticate() → Principal` (union: `UserPrincipal | ApiKeyPrincipal | SourcePrincipal`), then
`require(permission)` (§19). Webhook signature verification is a route-class dependency on the
ingest app. There is no route without an explicit auth dependency — a lint rule
(`no-undecorated-routes`) fails CI on any route lacking one, so "forgot to protect the
endpoint" is a build error, not a pentest finding.
