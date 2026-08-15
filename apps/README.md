# apps

Deployable services live here (FastAPI services, workers, CLIs) — one directory per app, each a
member of the `uv` workspace.

- `checkout-service`, `payment-service` — the toy system the signature scenario runs against
  (design doc §1.2). `payment-service` holds a real, configurable-size Postgres connection pool
  that genuinely exhausts under load when `DB_POOL_SIZE` is set too low.
- `toy-ops` — operational tooling for the toy system: the deploy script that produces the
  scenario's real "bad deploy" (and records it in AIC's own `deployment` table), and the load
  generator.

See `infra/kind/` for the cluster manifests these run on, and `make demo-up` / `make demo-deploy-bad`
/ `make demo-load` in the root `Makefile` to bring the scenario up.
