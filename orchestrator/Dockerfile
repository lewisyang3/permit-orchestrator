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
USER appuser
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8000"]
