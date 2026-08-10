# Multi-stage, multi-target: `docker build --target aic-api` produces an
# image containing only that service's dependency closure. Additional
# targets (aic-worker, aic-executor, aic-ingest) are added as those
# services are implemented (Phases 3/5/8).

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base
WORKDIR /workspace
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock* ./
COPY packages ./packages
COPY apps ./apps

FROM base AS aic-api
RUN uv sync --package aic-api --no-dev --frozen
EXPOSE 8000
CMD ["uv", "run", "--package", "aic-api", "uvicorn", "aic_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
