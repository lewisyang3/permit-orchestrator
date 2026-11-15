"""
Tests for UI & Cloud Run Probes Handler (orchestrator/handlers/ui.py).

Verifies:
1. Cloud Run probe endpoints (GET /health and GET /hello)
2. UI production distribution serving at root (GET /)
3. Fallback HTML delivery when dist directory is not built
4. Static asset delivery with accurate MIME types from /assets/
5. 404 status on missing static assets (no SPA leak into assets)
6. Directory traversal protection on static asset routes
7. Single Page Application (SPA) client-side routing fallback for non-API GET routes
8. Non-interference with API routes (/submit, /permits/{id}, /admin/fault, /live)
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.handlers.ui import (
    FALLBACK_HTML,
    get_assets_dir,
    get_dist_dir,
    get_index_path,
    router as ui_router,
)


@pytest.fixture
def app() -> FastAPI:
    """Fixture providing configured full orchestrator application."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Fixture providing TestClient for full orchestrator application."""
    return TestClient(app)


@pytest.fixture
def standalone_ui_client() -> TestClient:
    """Fixture providing TestClient for isolated UI router."""
    isolated_app = FastAPI(title="UI Isolated App")
    isolated_app.include_router(ui_router)
    return TestClient(isolated_app)


# ============================================================================
# 1. Cloud Run Probes
# ============================================================================

def test_health_probe_full_app(client: TestClient):
    """GET /health on full application returns 200 with healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")
    data = response.json()
    assert data.get("status") in ("healthy", "ok", "up")


def test_health_probe_isolated_router(standalone_ui_client: TestClient):
    """GET /health on isolated UI router returns 200 with healthy status."""
    response = standalone_ui_client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")
    assert response.json() == {"status": "healthy"}


def test_hello_probe_full_app(client: TestClient):
    """GET /hello on full application returns 200 with hello confirmation."""
    response = client.get("/hello")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")
    data = response.json()
    assert data.get("message") == "hello"
    assert data.get("status") == "ok"


def test_hello_probe_isolated_router(standalone_ui_client: TestClient):
    """GET /hello on isolated UI router returns 200 with hello confirmation."""
    response = standalone_ui_client.get("/hello")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")
    assert response.json() == {"message": "hello", "status": "ok"}


# ============================================================================
# 2. UI Root & Fallback Serving
# ============================================================================

def test_root_serves_production_index_html(client: TestClient):
    """GET / serves HTML content from frontend/dist/index.html."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    content = response.text
    assert "<!doctype html>" in content.lower() or "<html" in content.lower()
    assert 'id="root"' in content


def test_root_serves_fallback_when_dist_missing(monkeypatch: pytest.MonkeyPatch):
    """GET / serves fallback HTML when frontend/dist is missing or unbuilt."""
    with tempfile.TemporaryDirectory() as empty_dir:
        monkeypatch.setenv("FRONTEND_DIST_DIR", empty_dir)
        isolated_app = FastAPI()
        isolated_app.include_router(ui_router)
        isolated_client = TestClient(isolated_app)

        response = isolated_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "Permit Orchestrator" in response.text
        assert 'id="root"' in response.text


# ============================================================================
# 3. Static Asset Delivery
# ============================================================================

def test_static_asset_delivery(client: TestClient):
    """GET /assets/{filename} serves JavaScript and CSS bundles with proper MIME types."""
    assets_dir = get_assets_dir()
    if not assets_dir.exists():
        pytest.skip("frontend/dist/assets directory does not exist")

    asset_files = list(assets_dir.glob("*"))
    if not asset_files:
        pytest.skip("No asset files found in frontend/dist/assets")

    for file_path in asset_files:
        if not file_path.is_file():
            continue
        rel_path = file_path.name
        response = client.get(f"/assets/{rel_path}")
        assert response.status_code == 200, f"Failed to retrieve /assets/{rel_path}"

        content_type = response.headers.get("content-type", "")
        if rel_path.endswith(".js"):
            assert "javascript" in content_type
        elif rel_path.endswith(".css"):
            assert "text/css" in content_type

        assert len(response.content) > 0


def test_missing_static_asset_returns_404(client: TestClient):
    """Missing assets under /assets/ return 404 rather than falling back to SPA HTML."""
    response = client.get("/assets/nonexistent-bundle-xyz-999.js")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_static_asset_directory_traversal_blocked(client: TestClient):
    """Directory traversal attempts through /assets/ are blocked with 404."""
    response = client.get("/assets/%2e%2e%2f%2e%2e%2fpyproject.toml")
    assert response.status_code == 404


# ============================================================================
# 4. SPA Client-Side Routing Fallback
# ============================================================================

@pytest.mark.parametrize(
    "spa_path",
    [
        "/dashboard",
        "/permits",
        "/waterfall",
        "/settings",
        "/app/settings/monitoring",
    ],
)
def test_spa_routing_fallback(client: TestClient, spa_path: str):
    """Non-API client-side paths return the production index.html."""
    response = client.get(spa_path)
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert 'id="root"' in response.text


# ============================================================================
# 5. Non-Interference with Core API Routes
# ============================================================================

def test_api_submit_route_not_shadowed(client: TestClient):
    """POST /submit reaches submit handler and is not swallowed by SPA fallback."""
    response = client.post("/submit", json={})
    # Empty body should trigger pydantic validation 422, not SPA HTML
    assert response.status_code == 422
    assert "application/json" in response.headers.get("content-type", "")


def test_api_permit_status_404_not_shadowed(client: TestClient):
    """GET /permits/{id} for non-existent permit returns 404 JSON, not HTML index."""
    response = client.get("/permits/pm-nonexistent-999")
    assert response.status_code == 404
    assert response.headers.get("content-type", "").startswith("application/json")


def test_stream_live_route_not_shadowed(client: TestClient):
    """GET /live reaches stream handler and returns the live SSE inspection page."""
    response = client.get("/live")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Permit Orchestrator Live Stream" in response.text


def test_admin_fault_route_not_shadowed(client: TestClient):
    """GET /admin/fault reaches admin handler and returns JSON status."""
    response = client.get("/admin/fault")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")


@pytest.mark.parametrize(
    "api_path",
    [
        "/api/unknown",
        "/admin/nonexistent-endpoint",
        "/intake/nonexistent-doc",
    ],
)
def test_unmapped_api_routes_return_404_json(client: TestClient, api_path: str):
    """Requests matching API prefixes return 404 JSON rather than HTML SPA fallback."""
    response = client.get(api_path)
    assert response.status_code == 404
    assert response.headers.get("content-type", "").startswith("application/json")
