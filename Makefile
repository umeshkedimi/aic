.PHONY: install test lint fmt typecheck check migrate migrate-new

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
