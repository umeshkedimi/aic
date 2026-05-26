# Production Dockerfile for AIC
# Multi-stage build for minimal image size

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.12-slim as builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install .

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.12-slim as runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 aic \
    && useradd --uid 1000 --gid aic --shell /bin/bash --create-home aic

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY --chown=aic:aic src/ ./src/
COPY --chown=aic:aic migrations/ ./migrations/

# Switch to non-root user
USER aic

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run with gunicorn for production
CMD ["uvicorn", "aic.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
