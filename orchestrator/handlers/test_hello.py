"""
Tests for Hello-World and Health Check Handlers (orchestrator/handlers/hello.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.handlers.hello import (
    health,
    hello,
    router as hello_router,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def standalone_app() -> FastAPI:
    """FastAPI app containing only the hello router."""
    app = FastAPI(title="Hello Test App")
    app.include_router(hello_router)
    return app


@pytest.fixture
def orchestrator_client(tmp_path: Path) -> TestClient:
    """TestClient wired to the complete orchestrator app."""
    test_db = tmp_path / "hello_test.db"
    app = create_app(db_path=test_db)
    return TestClient(app)


def test_health_endpoint_direct():
    """Verify health async function directly."""
    result = asyncio.run(health())
    assert result == {"status": "healthy"}


def test_hello_endpoint_direct():
    """Verify hello async function directly."""
    result = asyncio.run(hello())
    assert isinstance(result, dict)
    assert result["status"] == "healthy"
    assert "Hello" in result["message"]
    assert result["service"] == "permit-orchestrator"
    assert "timestamp" in result

    # Validate timestamp format
    parsed_ts = datetime.fromisoformat(result["timestamp"])
    assert parsed_ts.tzinfo is not None


def test_health_endpoint_standalone(standalone_app: FastAPI):
    """Verify GET /health on standalone router."""
    with TestClient(standalone_app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


def test_hello_endpoint_standalone(standalone_app: FastAPI):
    """Verify GET /hello on standalone router."""
    with TestClient(standalone_app) as client:
        resp = client.get("/hello")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "message" in data
        assert "service" in data
        assert "timestamp" in data


def test_health_endpoint_orchestrator(orchestrator_client: TestClient):
    """Verify GET /health on full orchestrator app."""
    resp = orchestrator_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_hello_endpoint_orchestrator(orchestrator_client: TestClient):
    """Verify GET /hello on full orchestrator app."""
    resp = orchestrator_client.get("/hello")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "permit-orchestrator"
    assert "Hello" in data["message"]
    assert "timestamp" in data

    ts = datetime.fromisoformat(data["timestamp"])
    now = datetime.now(timezone.utc)
    # Timestamp should be within the last minute
    assert abs((now - ts).total_seconds()) < 60


@pytest.mark.anyio
async def test_hello_async_http():
    """Verify GET /hello using async HTTP client."""
    app = FastAPI()
    app.include_router(hello_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/hello")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


def test_hello_repeated_calls_generate_recent_timestamps(standalone_app: FastAPI):
    """Verify successive calls generate monotonic or non-stale timestamps."""
    with TestClient(standalone_app) as client:
        resp1 = client.get("/hello").json()
        t1 = datetime.fromisoformat(resp1["timestamp"])

        resp2 = client.get("/hello").json()
        t2 = datetime.fromisoformat(resp2["timestamp"])

        assert t2 >= t1
