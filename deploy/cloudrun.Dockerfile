# ==============================================================================
# Permit Orchestrator - Full Demo Multi-Service Cloud Run Container
# ==============================================================================
# Runs the orchestrator (FastAPI + Vite React SPA) alongside all 4 municipal
# department microservices (zoning:8001, fire:8002, water:8003, structural:8004).
# ==============================================================================

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

# Copy application source code and rules
COPY contract ./contract
COPY orchestrator ./orchestrator
COPY departments ./departments
COPY rules ./rules

# Copy compiled frontend production distribution
COPY frontend/dist ./frontend/dist

# Copy deployment and startup runner
COPY deploy/start_cloudrun.sh ./deploy/start_cloudrun.sh
RUN chmod +x ./deploy/start_cloudrun.sh && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Launch multi-service runner on Cloud Run $PORT (default 8080)
ENTRYPOINT ["/bin/bash", "./deploy/start_cloudrun.sh"]
