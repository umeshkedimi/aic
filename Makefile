.PHONY: sync up down migrate revision test lint typecheck check run-api

sync:
	uv sync --all-packages

up:
	docker compose up -d postgres redis

down:
	docker compose down

migrate:
	DATABASE_URL=$${DATABASE_URL:-postgresql+asyncpg://aic:aic@localhost:5432/aic} \
		uv run --package aic-database alembic upgrade head

revision:
	DATABASE_URL=$${DATABASE_URL:-postgresql+asyncpg://aic:aic@localhost:5432/aic} \
		uv run --package aic-database alembic revision --autogenerate -m "$(m)"

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy packages apps

check: lint typecheck test

run-api:
	uv run --package aic-api uvicorn aic_api.main:app --reload --port 8000
