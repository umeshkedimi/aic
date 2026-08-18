.PHONY: install test lint fmt typecheck check migrate migrate-new \
	demo-build demo-up demo-down demo-deploy-good demo-deploy-bad demo-load demo-status \
	observability-up observability-down demo-webhook-logs demo-prometheus-alerts

KIND_CLUSTER := aic-demo
DEMO_NAMESPACE := aic-demo

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

check: lint typecheck test

# Requires AIC_DATABASE_URL, e.g.:
#   AIC_DATABASE_URL=postgresql+psycopg://aic:aic@localhost:5432/aic make migrate
migrate:
	cd packages/database && uv run --project .. alembic upgrade head

migrate-new:
	cd packages/database && uv run --project .. alembic revision --autogenerate -m "$(MSG)"

# Toy system on kind (design doc §1.2/§1.3, T2). demo-deploy-good/-bad and
# demo-load talk to whatever cluster/kubectl context is current, so they
# also work against a cluster brought up by a previous demo-up.

demo-build:
	docker build -t checkout-service:dev -f apps/checkout-service/Dockerfile .
	docker build -t payment-service:dev -f apps/payment-service/Dockerfile .

# Requires AIC_DATABASE_URL (see .env.example) — the initial "good" deploy
# writes a real row to AIC's own system-of-record `deployment` table.
demo-up: demo-build
	kind get clusters 2>/dev/null | grep -qx $(KIND_CLUSTER) || \
		kind create cluster --config infra/kind/kind-config.yaml
	kind load docker-image checkout-service:dev payment-service:dev --name $(KIND_CLUSTER)
	kubectl apply -f infra/kind/namespace.yaml
	kubectl apply -f infra/kind/rbac.yaml
	kubectl apply -f infra/kind/postgres.yaml
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/postgres --timeout=90s
	kubectl apply -f infra/kind/checkout-service.yaml
	kubectl apply -f infra/kind/payment-service-service.yaml
	$(MAKE) demo-deploy-good
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/checkout-service --timeout=90s
	$(MAKE) observability-up
	@echo "demo is up — checkout-service: http://localhost:8080, prometheus: http://localhost:9090, alertmanager: http://localhost:9093"

demo-down:
	kind delete cluster --name $(KIND_CLUSTER)

# Observability stack (design doc §1.3/§1.4, T3): Prometheus + alert rules,
# Alertmanager (webhook -> stub receiver until T4's real aic-ingest lands),
# Loki + Promtail. Requires the checkout-service/payment-service Services to
# already exist (Prometheus scrape targets), so demo-up runs this last.
observability-up:
	kubectl apply -f infra/kind/observability/prometheus.yaml
	kubectl apply -f infra/kind/observability/alertmanager.yaml
	kubectl apply -f infra/kind/observability/webhook-stub.yaml
	kubectl apply -f infra/kind/observability/loki.yaml
	kubectl apply -f infra/kind/observability/promtail.yaml
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/prometheus --timeout=90s
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/alertmanager --timeout=90s
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/alert-webhook-stub --timeout=90s
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/loki --timeout=90s

observability-down:
	kubectl delete -f infra/kind/observability/promtail.yaml --ignore-not-found
	kubectl delete -f infra/kind/observability/loki.yaml --ignore-not-found
	kubectl delete -f infra/kind/observability/webhook-stub.yaml --ignore-not-found
	kubectl delete -f infra/kind/observability/alertmanager.yaml --ignore-not-found
	kubectl delete -f infra/kind/observability/prometheus.yaml --ignore-not-found

# Tail the stub webhook receiver to watch real Alertmanager POSTs land
# (T3's Done criterion — no faked alert payloads).
demo-webhook-logs:
	kubectl -n $(DEMO_NAMESPACE) logs -f deployment/alert-webhook-stub

demo-prometheus-alerts:
	kubectl -n $(DEMO_NAMESPACE) exec deployment/prometheus -- \
		wget -qO- http://localhost:9090/api/v1/alerts

demo-deploy-good:
	uv run --package aic-toy-ops deploy-payment-service --preset good

demo-deploy-bad:
	uv run --package aic-toy-ops deploy-payment-service --preset bad

demo-load:
	uv run --package aic-toy-ops load-generator --base-url http://localhost:8080

demo-status:
	kubectl -n $(DEMO_NAMESPACE) get deployments,pods,svc
