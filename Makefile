.PHONY: install test lint fmt typecheck check migrate migrate-new \
	demo-build demo-up demo-down demo-deploy-good demo-deploy-bad demo-load demo-status \
	observability-up observability-down demo-prometheus-alerts \
	eventbus-up eventbus-down demo-seed demo-services-up demo-services-down \
	demo-ingest-logs demo-correlator-logs demo-triage-logs demo-investigator-logs \
	demo-remediator-logs demo-approval-api-logs demo-approval-expirer-logs \
	demo-executor-logs demo-verifier-logs demo-scribe-logs \
	llm-up llm-down qdrant-up qdrant-down aic-approve

KIND_CLUSTER := aic-demo
DEMO_NAMESPACE := aic-demo
DEMO_RUN_DIR := /tmp/aic-demo
LITELLM_CONTAINER := aic-litellm
QDRANT_CONTAINER := aic-qdrant

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
	for app in payment-service checkout-service toy-ops aic-ingest aic-correlator aic-triage aic-investigator aic-remediator aic-approval-api aic-approval-expirer aic-cli aic-executor aic-verifier aic-scribe; do \
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
	$(MAKE) qdrant-up
	$(MAKE) demo-seed
	$(MAKE) demo-services-up
	@echo "demo is up — checkout-service: http://localhost:8080, prometheus: http://localhost:9090, alertmanager: http://localhost:9093, aic-ingest: http://localhost:8090, litellm: http://localhost:4000, qdrant: http://localhost:6333"

demo-down: demo-services-down llm-down qdrant-down
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

# Qdrant knowledge store (design doc §1.13, T12). A plain host-reachable
# Docker container, not a kind-cluster service — same reasoning as LiteLLM
# (T5): only host processes (aic-investigator's knowledge.search tool, the
# new aic-scribe poller) ever reach it. Pinned to the same image version
# testcontainers' QdrantContainer default uses (packages/agents' test
# suite), so dev/test/demo all talk to the same server version as the
# pinned `qdrant-client` — avoids a client/server version-skew warning.
qdrant-up:
	docker rm -f $(QDRANT_CONTAINER) >/dev/null 2>&1 || true
	docker run -d --name $(QDRANT_CONTAINER) -p 6333:6333 -p 6334:6334 qdrant/qdrant:v1.16.2

qdrant-down:
	docker rm -f $(QDRANT_CONTAINER) >/dev/null 2>&1 || true

# Idempotent — safe to rerun against an already-seeded cluster.
demo-seed:
	uv run --package aic-toy-ops seed-service-dependencies

# aic-ingest/aic-correlator/aic-triage/aic-investigator/aic-remediator/
# aic-approval-api/aic-approval-expirer/aic-executor/aic-verifier/
# aic-scribe (T4, T6, T7, T8, T9, T10, T11, T12) run as background host
# processes, like apps/toy-ops's deploy/load-generator scripts — see the
# design note in infra/kind/eventbus/redpanda.yaml. aic-triage/
# aic-investigator/aic-remediator/aic-scribe additionally require the
# litellm proxy (make llm-up) to be reachable; aic-investigator and
# aic-scribe additionally require Qdrant (make qdrant-up) to be reachable.
# aic-investigator mints its own read-only aic-investigator ServiceAccount
# token, and aic-remediator/aic-executor each mint the write-scoped
# aic-executor ServiceAccount token (aic-remediator only to attach a
# dry-run to the approval card, aic-executor to actually execute) from the
# current kubeconfig at startup (infra/kind/rbac.yaml, T2) — the demo
# cluster must already exist. aic-verifier and aic-scribe hold neither K8s
# credential. aic-approval-api needs AIC_APPROVAL_API_IDENTITIES set (see
# .env.example) to authenticate any decision at all.
demo-services-up:
	mkdir -p $(DEMO_RUN_DIR)
	uv run --package aic-ingest aic-ingest > $(DEMO_RUN_DIR)/aic-ingest.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-ingest.pid
	uv run --package aic-correlator aic-correlator > $(DEMO_RUN_DIR)/aic-correlator.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-correlator.pid
	uv run --package aic-triage aic-triage > $(DEMO_RUN_DIR)/aic-triage.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-triage.pid
	uv run --package aic-investigator aic-investigator > $(DEMO_RUN_DIR)/aic-investigator.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-investigator.pid
	uv run --package aic-remediator aic-remediator > $(DEMO_RUN_DIR)/aic-remediator.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-remediator.pid
	uv run --package aic-approval-api aic-approval-api > $(DEMO_RUN_DIR)/aic-approval-api.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-approval-api.pid
	uv run --package aic-approval-expirer aic-approval-expirer > $(DEMO_RUN_DIR)/aic-approval-expirer.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-approval-expirer.pid
	uv run --package aic-executor aic-executor > $(DEMO_RUN_DIR)/aic-executor.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-executor.pid
	uv run --package aic-verifier aic-verifier > $(DEMO_RUN_DIR)/aic-verifier.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-verifier.pid
	uv run --package aic-scribe aic-scribe > $(DEMO_RUN_DIR)/aic-scribe.log 2>&1 & \
		echo $$! > $(DEMO_RUN_DIR)/aic-scribe.pid
	@echo "aic-ingest, aic-correlator, aic-triage, aic-investigator, aic-remediator, aic-approval-api, aic-approval-expirer, aic-executor, aic-verifier, and aic-scribe started — logs: $(DEMO_RUN_DIR)/*.log"

demo-services-down:
	-[ -f $(DEMO_RUN_DIR)/aic-ingest.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-ingest.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-ingest.pid
	-[ -f $(DEMO_RUN_DIR)/aic-correlator.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-correlator.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-correlator.pid
	-[ -f $(DEMO_RUN_DIR)/aic-triage.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-triage.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-triage.pid
	-[ -f $(DEMO_RUN_DIR)/aic-investigator.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-investigator.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-investigator.pid
	-[ -f $(DEMO_RUN_DIR)/aic-remediator.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-remediator.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-remediator.pid
	-[ -f $(DEMO_RUN_DIR)/aic-approval-api.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-approval-api.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-approval-api.pid
	-[ -f $(DEMO_RUN_DIR)/aic-approval-expirer.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-approval-expirer.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-approval-expirer.pid
	-[ -f $(DEMO_RUN_DIR)/aic-executor.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-executor.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-executor.pid
	-[ -f $(DEMO_RUN_DIR)/aic-verifier.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-verifier.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-verifier.pid
	-[ -f $(DEMO_RUN_DIR)/aic-scribe.pid ] && kill $$(cat $(DEMO_RUN_DIR)/aic-scribe.pid) 2>/dev/null; \
		rm -f $(DEMO_RUN_DIR)/aic-scribe.pid

demo-ingest-logs:
	tail -f $(DEMO_RUN_DIR)/aic-ingest.log

demo-correlator-logs:
	tail -f $(DEMO_RUN_DIR)/aic-correlator.log

demo-triage-logs:
	tail -f $(DEMO_RUN_DIR)/aic-triage.log

demo-investigator-logs:
	tail -f $(DEMO_RUN_DIR)/aic-investigator.log

demo-remediator-logs:
	tail -f $(DEMO_RUN_DIR)/aic-remediator.log

demo-approval-api-logs:
	tail -f $(DEMO_RUN_DIR)/aic-approval-api.log

demo-approval-expirer-logs:
	tail -f $(DEMO_RUN_DIR)/aic-approval-expirer.log

demo-executor-logs:
	tail -f $(DEMO_RUN_DIR)/aic-executor.log

demo-verifier-logs:
	tail -f $(DEMO_RUN_DIR)/aic-verifier.log

demo-scribe-logs:
	tail -f $(DEMO_RUN_DIR)/aic-scribe.log

# `make aic-approve INCIDENT_ID=<uuid>` — the one-command CLI surface
# (design doc §1.10 APPROVE row, T9). Requires AIC_CLI_DECIDER_ID (and
# optionally AIC_CLI_DECIDER_ROLES) in the environment or .env.
aic-approve:
	uv run --package aic-cli aic approve $(INCIDENT_ID)

demo-deploy-good:
	uv run --package aic-toy-ops deploy-payment-service --preset good

demo-deploy-bad:
	uv run --package aic-toy-ops deploy-payment-service --preset bad

demo-load:
	uv run --package aic-toy-ops load-generator --base-url http://localhost:8080

demo-status:
	kubectl -n $(DEMO_NAMESPACE) get deployments,pods,svc
