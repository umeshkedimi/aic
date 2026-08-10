# ADR 0003: Local Kubernetes (kind) as the remediation target

## Status
Accepted (2026-08-10)

## Context
`RollbackDeployment` needs a real target to act on. Docker Compose would be faster to stand up and
just as capable of demonstrating the policy → approval → execution → verification mechanics. But
the mandate explicitly lists Kubernetes deployment knowledge among what the project must prove,
and a `kubectl rollout undo` against a real Deployment object is a materially more realistic
remediation action than recreating a Compose container — it exercises the actual primitive
(ReplicaSets, rollout history, readiness gates) that a real production rollback would use.

## Decision
Run the signature scenario's toy system (`checkout-service`, `payment-service`, Postgres, Redis)
in a local `kind` cluster, in a dedicated namespace (e.g. `aic-demo`). The "bad deploy" is a real
`kubectl apply`/`kubectl set image` changing `payment-service`'s pod spec (reduced
`DB_POOL_SIZE` env var). Remediation executes `kubectl rollout undo deployment/payment-service`
(or the Python K8s client equivalent) against the previous ReplicaSet.

## Consequences
- The executor's write credential is a dedicated `ServiceAccount` bound by a `Role` scoped to
  `get/list/patch` on `deployments` in the `aic-demo` namespace only — real least-privilege, not
  a conceptual placeholder. The investigation path's tools use a separate, read-only-bound
  `ServiceAccount`.
- Local dev now requires `kind` + `kubectl` + cluster bootstrap (namespace, RBAC, manifests) before
  the signature scenario can run — a real setup cost, accepted for the realism it buys.
- Dry-run uses `kubectl rollout undo --dry-run=server` (or the K8s API's dry-run field) so the
  approval card can show the actual diff before a human approves it.
- The `get_deployment_history` investigation tool reads `kubectl rollout history` / ReplicaSet
  metadata plus AIC's own `deployment` table (recorded at deploy time) — both real, not synthetic.
