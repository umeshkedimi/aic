# 19. Authorization

AIC runs **two deliberately separate authorization systems**, because they answer different
questions about different kinds of actors:

| System | Question | Actors | Engine |
|---|---|---|---|
| **RBAC** | May this *principal* perform this *API operation*? | humans, API keys | permission check in `aic-api` |
| **Action policy** | May this *remediation action* run in this *environment*, and who must approve? | proposed actions (regardless of proposer) | `PolicyEngine` (domain layer) |

Collapsing these into one system is a classic design error: RBAC governs people and evolves
with the org chart; action policy governs blast radius and evolves with operational confidence.
They change at different speeds, are administered by different people, and an entry in one must
never satisfy the other.

## 19.1 RBAC model

Roles are additive permission bundles; a principal holds a set of roles.

| Permission | viewer | operator | approver | admin |
|---|:-:|:-:|:-:|:-:|
| incidents:read, knowledge:read, policies:read | ✓ | ✓ | ✓ | ✓ |
| incidents:create, incidents:operate (escalate/resolve/close/reclassify) | | ✓ | | ✓ |
| knowledge:write | | ✓ | | ✓ |
| approvals:decide | | | ✓ | ✓ |
| policies:write, integrations:admin, iam:admin, audit:read | | | | ✓ |

Notes on the deliberate edges:

- **`operator` does not include `approvals:decide`.** The person driving the incident and the
  person authorizing infrastructure change are different hats even when the org is small —
  where they're the same human, grant both roles explicitly and the audit log shows it.
- **`admin` includes `approvals:decide` but policy can demand more** — e.g. quorum 2 with role
  `approver` means an admin alone still can't push a prod action through (see 19.3).
- Roles are stored as `iam.role_assignment` rows (who, role, granted_by, granted_at,
  expires_at nullable). Time-boxed grants supported from day one — "approver for this
  on-call week" is a normal enterprise pattern.

## 19.2 Enforcement

- Route level: `require("approvals:decide")` FastAPI dependency — declarative, next to the
  route, greppable, CI-linted for presence (§18.6).
- Object level where role isn't enough: approval decisions verify the principal satisfies the
  *approval request's* `required_roles` (policy may demand `approver`, or later
  `sre-senior`), and quorum rules (19.3).
- Denials return RFC 9457 `403` with the missing permission named, and are **audit-logged with
  full context** — authz denials on approval endpoints are a `SecurityEvent` (someone probing
  the gate is signal, T3).

## 19.3 Approval quorum semantics (where RBAC meets action policy)

The policy decision `require_approval(quorum=N, roles=[...])` compiles into an
`approval_request` with those requirements. Rules enforced in one serializable transaction per
decision:

1. Decider must hold a required role *at decision time* (fresh DB check, not just JWT claims —
   §18.1).
2. One decision per principal per request (unique constraint).
3. **Proposer exclusion:** the incident's `escalated`-state human operator can approve; but any
   principal recorded as having *manually edited* the proposal (roadmap feature) is excluded —
   author ≠ approver, the classic change-management control.
4. Quorum counts only `approve` decisions from distinct principals; any `reject` short-circuits
   to rejected (reject-wins — a concerned reviewer beats N enthusiastic ones).
5. Expiry beats everything: decisions after `expires_at` are refused (the workflow may have
   already safely expired the request).

## 19.4 API-key scopes

API keys use the same permission vocabulary, restricted: keys **cannot** carry
`approvals:decide` or `iam:admin` — machine approval of remediation is contradiction-by-design
(the entire point of the gate is a human), and IAM changes from a leaked key is T3's worst
case. A key requesting those scopes fails at creation.

## 19.5 Environment scoping (the third axis)

Role assignments optionally carry an environment filter: `approver(env=staging)` can approve
staging actions only. Implementation: approval-request matching adds `environment ∈
assignment.environments`. This is how a team lets juniors run staging autonomously while prod
approvals stay senior — same mechanism, no special cases. Service-level scoping
(`operator(service=payments)`) is deferred to the multi-team roadmap (§36) — the model
supports it (filters are a list of predicates), but MVP ships without the admin UX to manage it
responsibly.

## 19.6 The action-policy side (summary; details in §23)

For symmetry, what the `PolicyEngine` evaluates per proposed action: action type × environment
× blast-radius conditions (typed predicates over action params: `replicas_delta ≤ 2`,
`min_replicas ≥ 1`, target allowlists) → `auto_approve | require_approval(quorum, roles) |
forbid`, most-restrictive-wins across matching rules, **default-deny**: an action type with no
matching rule is `forbid`. New catalog actions are therefore un-runnable until someone
consciously writes policy for them — shipping a new action cannot silently widen autonomy.
