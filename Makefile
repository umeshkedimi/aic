.PHONY: help install dev test lint format typecheck clean docker-up docker-down docker-logs migrate seed

# Default target
help:
	@echo "AIC - AI Incident Commander"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Development:"
	@echo "  install      Install dependencies"
	@echo "  dev          Run development server"
	@echo "  test         Run tests"
	@echo "  test-cov     Run tests with coverage"
	@echo "  lint         Run linter"
	@echo "  format       Format code"
	@echo "  typecheck    Run type checker"
	@echo "  clean        Clean build artifacts"
	@echo ""
	@echo "Docker:"
	@echo "  docker-up    Start all services"
	@echo "  docker-down  Stop all services"
	@echo "  docker-logs  View logs"
	@echo "  docker-build Build containers"
	@echo ""
	@echo "Database:"
	@echo "  migrate      Run database migrations"
	@echo "  migrate-new  Create new migration"
	@echo "  seed         Seed sample data"

# =============================================================================
# Development
# =============================================================================

install:
	pip install -e ".[dev]"
	pre-commit install

dev:
	uvicorn aic.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=src/aic --cov-report=term-missing --cov-report=html

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

format:
	black src/ tests/
	ruff check src/ tests/ --fix

typecheck:
	mypy src/

check: lint typecheck test
	@echo "All checks passed!"

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# =============================================================================
# Docker
# =============================================================================

docker-up:
	docker compose up -d

docker-up-build:
	docker compose up -d --build

docker-down:
	docker compose down

docker-down-volumes:
	docker compose down -v

docker-logs:
	docker compose logs -f

docker-logs-api:
	docker compose logs -f api

docker-build:
	docker compose build

docker-ps:
	docker compose ps

docker-restart:
	docker compose restart api

# =============================================================================
# Database
# =============================================================================

migrate:
	alembic upgrade head

migrate-new:
	@read -p "Migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

migrate-down:
	alembic downgrade -1

migrate-history:
	alembic history

seed:
	python scripts/seed_knowledge.py

# =============================================================================
# Utilities
# =============================================================================

shell:
	docker compose exec api python

psql:
	docker compose exec postgres psql -U aic -d aic

redis-cli:
	docker compose exec redis redis-cli

logs-tail:
	tail -f logs/*.log 2>/dev/null || echo "No log files found"
