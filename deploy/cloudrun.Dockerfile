# ==============================================================================
# Multi-stage Dockerfile for Permit Orchestrator + UI on Google Cloud Run
# ==============================================================================

# Stage 1: Build the React frontend
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Production Python runtime with orchestrator and department services
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
    && mkdir -p /data /tmp/uploads && chown -R appuser:appuser /data /tmp/uploads

# Copy application source code
COPY contract ./contract
COPY orchestrator ./orchestrator
COPY departments ./departments
COPY rules ./rules
COPY deploy ./deploy

# Copy built frontend assets from builder stage
COPY --from=frontend-builder /frontend/dist ./frontend/dist

RUN chmod +x ./deploy/entrypoint.sh && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Cloud Run injects $PORT (default 8080)
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
