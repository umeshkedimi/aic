# Build context is the repo root (`docker build -f apps/checkout-service/Dockerfile .`)
# — see apps/payment-service/Dockerfile for why the whole workspace is copied in.
FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.11.28

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps

RUN uv sync --frozen --no-dev --package aic-checkout-service

ENV PATH="/workspace/.venv/bin:${PATH}"

EXPOSE 8000

CMD ["uvicorn", "checkout_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
