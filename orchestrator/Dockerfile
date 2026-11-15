# Stage 1: build the React trace UI so the orchestrator can serve it at /
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# Stage 2: orchestrator runtime
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project \
    && useradd --uid 10001 --create-home appuser \
    && mkdir -p /data /app/uploads && chown -R appuser:appuser /data /app/uploads
COPY contract ./contract
COPY orchestrator ./orchestrator
COPY rules ./rules
COPY --from=frontend /frontend/dist ./frontend/dist
USER appuser
# Cloud Run injects PORT (default 8080); Compose and local runs fall back to 8000.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn orchestrator.app:app --host 0.0.0.0 --port \"${PORT}\""]
