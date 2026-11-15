# Dockerfile for Permit Orchestrator on Google Cloud Run
FROM python:3.13-slim

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

WORKDIR /app

# Copy dependency manifests and install locked production dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project \
    && useradd --uid 10001 --create-home appuser \
    && mkdir -p /data && chown -R appuser:appuser /data

# Copy application source code
COPY contract ./contract
COPY orchestrator ./orchestrator
COPY rules ./rules

USER appuser

EXPOSE 8080

# Cloud Run injects $PORT (default 8080)
CMD ["sh", "-c", "exec python -m uvicorn orchestrator.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
