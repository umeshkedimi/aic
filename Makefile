.PHONY: install test lint fmt typecheck check migrate migrate-new \
	demo-build demo-up demo-down demo-deploy-good demo-deploy-bad demo-load demo-status \
	observability-up observability-down demo-prometheus-alerts \
	eventbus-up eventbus-down demo-seed demo-services-up demo-services-down \
	demo-ingest-logs demo-correlator-logs llm-up llm-down

KIND_CLUSTER := aic-demo
DEMO_NAMESPACE := aic-demo
DEMO_RUN_DIR := /tmp/aic-demo
LITELLM_CONTAINER := aic-litellm

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

# One combined pass for packages/* (unhyphenated dir names — safe to check
# together), then one pass per hyphenated apps/* dir — see the comment on
# [tool.mypy] in pyproject.toml for why they can't be combined.
typecheck:
	uv run mypy packages/common/src packages/common/tests \
		packages/domain/src packages/domain/tests \
		packages/database/src packages/database/tests packages/database/alembic \
		packages/contracts/src packages/contracts/tests \
		packages/eventbus/src packages/eventbus/tests \
		packages/agents/src packages/agents/tests
	for app in payment-service checkout-service toy-ops aic-ingest aic-correlator; do \
		uv run mypy apps/$$app/src apps/$$app/tests || exit 1; \
	done

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
	$(MAKE) eventbus-up
	$(MAKE) llm-up
	$(MAKE) demo-seed
	$(MAKE) demo-services-up
	@echo "demo is up — checkout-service: http://localhost:8080, prometheus: http://localhost:9090, alertmanager: http://localhost:9093, aic-ingest: http://localhost:8090, litellm: http://localhost:4000"

demo-down: demo-services-down llm-down
	kind delete cluster --name $(KIND_CLUSTER)

# Observability stack (design doc §1.3/§1.4, T3): Prometheus + alert rules,
# Alertmanager (webhook -> the real aic-ingest, T4), Loki + Promtail.
# Requires the checkout-service/payment-service Services to already exist
# (Prometheus scrape targets), so demo-up runs this before eventbus-up.
observability-up:
	kubectl apply -f infra/kind/observability/prometheus.yaml
	kubectl apply -f infra/kind/observability/alertmanager.yaml
	kubectl apply -f infra/kind/observability/loki.yaml
	kubectl apply -f infra/kind/observability/promtail.yaml
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/prometheus --timeout=90s
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/alertmanager --timeout=90s
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/loki --timeout=90s

observability-down:
	kubectl delete -f infra/kind/observability/promtail.yaml --ignore-not-found
	kubectl delete -f infra/kind/observability/loki.yaml --ignore-not-found
	kubectl delete -f infra/kind/observability/alertmanager.yaml --ignore-not-found
	kubectl delete -f infra/kind/observability/prometheus.yaml --ignore-not-found

demo-prometheus-alerts:
	kubectl -n $(DEMO_NAMESPACE) exec deployment/prometheus -- \
		wget -qO- http://localhost:9090/api/v1/alerts

# Kafka-compatible event bus (design doc §1.8, ADR 0002, T4). See the design
# note in infra/kind/eventbus/redpanda.yaml for why aic-ingest/aic-correlator
# reach it as host processes rather than in-cluster pods.
eventbus-up:
	kubectl apply -f infra/kind/eventbus/redpanda.yaml
	kubectl -n $(DEMO_NAMESPACE) rollout status deployment/redpanda --timeout=90s

eventbus-down:
	kubectl delete -f infra/kind/eventbus/redpanda.yaml --ignore-not-found

# LiteLLM proxy (T5, ADR 0004). A plain host-reachable Docker container,
# not a kind-cluster service — see the design note in
# infra/litellm/litellm_config.yaml. Requires ANTHROPIC_API_KEY and
# LITELLM_MASTER_KEY in .env (see .env.example).
llm-up:
	docker rm -f $(LITELLM_CONTAINER) >/dev/null 2>&1 || true
	docker run -d --name $(LITELLM_CONTAINER) \
		--env-file .env \
		-p 4000:4000 \
		-v $(PWD)/infra/litellm/litellm_config.yaml:/app/config.yaml \
		ghcr.io/berriai/litellm:main-stable \
		--config /app/config.yaml --port 4000

llm-down:
	docker rm -f $(LITELLM_CONTAINER) >/dev/null 2>&1 || true

# Idempotent — safe to rerun against an already-seeded cluster.
demo-seed:
	uv run --package aic-toy-ops seed-service-dependencies

# aic-ingest/aic-correlator (T4) run as background host processes, like
# apps/toy-ops's deploy/load-generator scripts — see the design note in
# infra/kind/eventbus/redpanda.yaml.
demo-services-up:
	mkdir -p $(DEMO_RUN_DIR)
	uv run --package aic-ingest aic-ingest > $(DEMO_RUN_DIR)/aic-ingest.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-ingest.pid
	uv run --package aic-correlator aic-correlator > $(DEMO_RUN_DIR)/aic-correlator.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-correlator.pid
	@echo "aic-ingest and aic-correlator started — logs: $(DEMO_RUN_DIR)/*.log"

demo-services-down:
	-[ -f $(DEMO_RUN_DIR)/aic-ingest.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-ingest.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-ingest.pid
	-[ -f $(DEMO_RUN_DIR)/aic-correlator.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-correlator.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-correlator.pid

demo-ingest-logs:
	tail -f $(DEMO_RUN_DIR)/aic-ingest.log

demo-correlator-logs:
	tail -f $(DEMO_RUN_DIR)/aic-correlator.log

demo-deploy-good:
	uv run --package aic-toy-ops deploy-payment-service --preset good

demo-deploy-bad:
	uv run --package aic-toy-ops deploy-payment-service --preset bad

demo-load:
	uv run --package aic-toy-ops load-generator --base-url http://localhost:8080

demo-status:
	kubectl -n $(DEMO_NAMESPACE) get deployments,pods,svc
