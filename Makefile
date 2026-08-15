.PHONY: install test lint fmt typecheck check migrate migrate-new \
	demo-build demo-up demo-down demo-deploy-good demo-deploy-bad demo-load demo-status

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
	@echo "demo is up — checkout-service: http://localhost:8080"

demo-down:
	kind delete cluster --name $(KIND_CLUSTER)

demo-deploy-good:
	uv run --package aic-toy-ops deploy-payment-service --preset good

demo-deploy-bad:
	uv run --package aic-toy-ops deploy-payment-service --preset bad

demo-load:
	uv run --package aic-toy-ops load-generator --base-url http://localhost:8080

demo-status:
	kubectl -n $(DEMO_NAMESPACE) get deployments,pods,svc
