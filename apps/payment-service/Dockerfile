# Build context is the repo root (`docker build -f apps/payment-service/Dockerfile .`)
# because this is a uv workspace: payment-service depends on packages/common,
# and uv needs the whole workspace manifest (root pyproject.toml + uv.lock)
# present to resolve it, even though only payment-service's deps get synced.
FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.11.28

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps

RUN uv sync --frozen --no-dev --package aic-payment-service

ENV PATH="/workspace/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uvicorn", "payment_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
