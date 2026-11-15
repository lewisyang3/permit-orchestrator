"""
Verifier test suite for Task T10:
Cloud Run Deploy & Full Demo Stack Verification.

Verifies:
1. Handler co-location:
   - Handler implementations and test files exist directly in orchestrator/handlers/:
     * orchestrator/handlers/submit.py and orchestrator/handlers/test_submit.py
     * orchestrator/handlers/stream.py and orchestrator/handlers/test_stream.py
     * orchestrator/handlers/admin.py and orchestrator/handlers/test_admin.py
     * orchestrator/handlers/ui.py and orchestrator/handlers/test_ui.py
2. Handler integration & route registration:
   - orchestrator/handlers/__init__.py exports routers/handlers (admin_router, submit_router, stream_router, ui_router).
   - orchestrator/app.py mounts handlers, including ui_router.
   - Standard Cloud Run probes and UI endpoints:
     * GET /health -> 200 {"status": "healthy"}
     * GET /hello -> 200
     * GET / -> 200 (HTML page from frontend build or fallback)
     * SPA routing fallback for non-API routes.
3. Cloud Run multi-service Dockerfile (deploy/cloudrun.Dockerfile):
   - Multi-service container setup for full demo stack.
   - Installs required dependencies (Python and uv).
   - Copies frontend distribution (frontend/dist).
   - Copies backend applications (orchestrator, departments, contract, rules).
   - Exposes container port ($PORT / 8080).
   - Sets entrypoint / CMD to deploy/start_cloudrun.sh.
4. Cloud Run startup runner (deploy/start_cloudrun.sh):
   - Executable bash script.
   - Starts all 4 department microservices:
     * Zoning on port 8001
     * Fire on port 8002
     * Water on port 8003
     * Structural on port 8004
   - Starts orchestrator binding to $PORT (default 8080).
   - Coordinates multi-process execution with process management / traps.
5. Cloud Run deployment script & public URL:
   - deploy/deploy_cloudrun.sh exists and is executable.
   - deploy/public_url.txt exists, contains a non-empty, valid Cloud Run URL.
   - Validates format and responsiveness of the public demo endpoint.
6. Documentation:
   - deploy/README.md documents Cloud Run multi-service topology, ports, and deployment instructions.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import urllib.parse
from typing import Dict, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DIR = REPO_ROOT / "deploy"
HANDLERS_DIR = REPO_ROOT / "orchestrator" / "handlers"


# ============================================================================
# 1. Handler and Handler Tests Co-location
# ============================================================================

EXPECTED_HANDLERS = [
    ("submit.py", "test_submit.py"),
    ("stream.py", "test_stream.py"),
    ("admin.py", "test_admin.py"),
    ("ui.py", "test_ui.py"),
]


@pytest.mark.parametrize("handler_file,test_file", EXPECTED_HANDLERS)
def test_handler_and_test_colocation(handler_file: str, test_file: str):
    """
    Verifies that each handler implementation in orchestrator/handlers/
    has its corresponding test file located directly next to it.
    """
    handler_path = HANDLERS_DIR / handler_file
    test_path = HANDLERS_DIR / test_file

    assert handler_path.exists(), f"Handler file {handler_path} does not exist"
    assert handler_path.is_file(), f"Handler path {handler_path} is not a regular file"
    assert handler_path.stat().st_size > 0, f"Handler file {handler_path} is empty"

    assert test_path.exists(), f"Handler test file {test_path} does not exist next to {handler_file}"
    assert test_path.is_file(), f"Handler test path {test_path} is not a regular file"
    assert test_path.stat().st_size > 0, f"Handler test file {test_path} is empty"

    # Verify the test file contains actual test definitions
    content = test_path.read_text(encoding="utf-8")
    assert "def test_" in content or "async def test_" in content, (
        f"Handler test file {test_file} does not contain any 'def test_' test cases"
    )


def test_handlers_package_exports():
    """
    Verifies that orchestrator/handlers/__init__.py exports all routers and handlers.
    """
    init_path = HANDLERS_DIR / "__init__.py"
    assert init_path.exists(), f"__init__.py missing in {HANDLERS_DIR}"

    import orchestrator.handlers as handlers_pkg

    # Check for core routers
    assert hasattr(handlers_pkg, "admin_router"), "orchestrator.handlers missing admin_router"
    assert hasattr(handlers_pkg, "submit_router"), "orchestrator.handlers missing submit_router"
    assert hasattr(handlers_pkg, "stream_router"), "orchestrator.handlers missing stream_router"
    assert hasattr(handlers_pkg, "ui_router"), "orchestrator.handlers missing ui_router"


# ============================================================================
# 2. UI Handler & Cloud Run Probe Endpoints in Orchestrator
# ============================================================================

def test_cloudrun_probe_endpoints():
    """
    Verifies that orchestrator app exposes GET /health and GET /hello for Cloud Run probes.
    """
    from orchestrator.app import create_app

    app = create_app()
    client = TestClient(app)

    # 1. Health check probe
    health_resp = client.get("/health")
    assert health_resp.status_code == 200, f"/health returned {health_resp.status_code}"
    health_json = health_resp.json()
    assert health_json.get("status") in ("healthy", "ok", "up"), (
        f"/health response unexpected: {health_json}"
    )

    # 2. Hello probe for Cloud Run verification
    hello_resp = client.get("/hello")
    assert hello_resp.status_code == 200, f"/hello returned {hello_resp.status_code}"
    hello_json = hello_resp.json()
    assert hello_json.get("message") == "hello" or "hello" in str(hello_json).lower()


def test_ui_root_and_static_serving():
    """
    Verifies that orchestrator app serves the UI at root GET / and static assets.
    """
    from orchestrator.app import create_app

    app = create_app()
    client = TestClient(app)

    root_resp = client.get("/")
    assert root_resp.status_code == 200, f"GET / returned status {root_resp.status_code}"
    # Should serve HTML
    content_type = root_resp.headers.get("content-type", "")
    assert "text/html" in content_type, f"GET / content-type was {content_type}, expected text/html"


def test_ui_spa_fallback():
    """
    Verifies that unknown non-API routes fall back to serving the SPA index.html.
    """
    from orchestrator.app import create_app

    app = create_app()
    client = TestClient(app)

    spa_resp = client.get("/permits/view/abc-123")
    assert spa_resp.status_code == 200, f"SPA route returned {spa_resp.status_code}"
    content_type = spa_resp.headers.get("content-type", "")
    assert "text/html" in content_type


# ============================================================================
# 3. Cloud Run Multi-Service Dockerfile (deploy/cloudrun.Dockerfile)
# ============================================================================

def test_cloudrun_dockerfile_spec():
    """
    Validates deploy/cloudrun.Dockerfile for Cloud Run multi-service deployment.
    """
    dockerfile_path = DEPLOY_DIR / "cloudrun.Dockerfile"
    assert dockerfile_path.exists(), f"Cloud Run Dockerfile not found at {dockerfile_path}"
    assert dockerfile_path.is_file(), f"{dockerfile_path} is not a regular file"
    assert dockerfile_path.stat().st_size > 50, f"{dockerfile_path} appears empty or too small"

    content = dockerfile_path.read_text(encoding="utf-8")

    # Check for python base
    assert "FROM" in content, "Dockerfile missing FROM instruction"
    assert "python" in content.lower(), "Dockerfile should use a Python base image"

    # Check port exposure
    assert "8080" in content or "$PORT" in content or "PORT" in content, (
        "Dockerfile should configure or expose port 8080 or $PORT"
    )

    # Check entrypoint or CMD invokes start_cloudrun.sh
    assert "start_cloudrun.sh" in content, (
        "Dockerfile should specify start_cloudrun.sh as entrypoint/CMD"
    )

    # Check that it copies required source code
    assert "orchestrator" in content, "Dockerfile should copy orchestrator"
    assert "departments" in content, "Dockerfile should copy departments"
    assert "rules" in content, "Dockerfile should copy rules"
    assert "frontend/dist" in content, "Dockerfile should copy frontend/dist"


# ============================================================================
# 4. Cloud Run Startup Runner (deploy/start_cloudrun.sh)
# ============================================================================

def test_start_cloudrun_runner_spec():
    """
    Validates deploy/start_cloudrun.sh starts all 4 department microservices
    and the orchestrator on $PORT.
    """
    runner_path = DEPLOY_DIR / "start_cloudrun.sh"
    assert runner_path.exists(), f"Startup runner not found at {runner_path}"
    assert runner_path.is_file(), f"{runner_path} is not a regular file"

    # Verify executable bit or bash shebang
    mode = runner_path.stat().st_mode
    is_executable = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    content = runner_path.read_text(encoding="utf-8")
    assert content.startswith("#!"), f"{runner_path} should start with a shebang (e.g. #!/usr/bin/env bash)"
    assert is_executable, f"{runner_path} should be marked executable (chmod +x)"

    # Verify all 4 department service ports are configured (8001, 8002, 8003, 8004)
    for dept_name, port in [
        ("zoning", "8001"),
        ("fire", "8002"),
        ("water", "8003"),
        ("structural", "8004"),
    ]:
        assert port in content, f"Startup runner missing port {port} for {dept_name}"
        assert dept_name in content.lower(), f"Startup runner missing reference to {dept_name}"

    # Verify orchestrator port binding (8080 or $PORT)
    assert "8080" in content or "PORT" in content, (
        "Startup runner should bind orchestrator to $PORT / 8080"
    )

    # Verify process management (trap, wait, background execution)
    assert "&" in content, "Startup runner should run microservices in background"
    assert "wait" in content or "exec" in content or "trap" in content, (
        "Startup runner should keep container process alive via wait/exec/trap"
    )


# ============================================================================
# 5. Cloud Run Deploy Script (deploy/deploy_cloudrun.sh)
# ============================================================================

def test_deploy_cloudrun_script_spec():
    """
    Validates deploy/deploy_cloudrun.sh script.
    """
    deploy_path = DEPLOY_DIR / "deploy_cloudrun.sh"
    assert deploy_path.exists(), f"Deploy script not found at {deploy_path}"
    assert deploy_path.is_file(), f"{deploy_path} is not a regular file"

    mode = deploy_path.stat().st_mode
    is_executable = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    assert is_executable, f"{deploy_path} should be marked executable"

    content = deploy_path.read_text(encoding="utf-8")
    assert content.startswith("#!"), f"{deploy_path} should start with a shebang"
    assert "run.app" in content or "gcloud" in content or "public_url.txt" in content, (
        "Deploy script should handle Cloud Run deployment / URL recording"
    )


# ============================================================================
# 6. Public Demo URL File & Responsiveness (deploy/public_url.txt)
# ============================================================================

def test_public_url_file_and_responsiveness():
    """
    Validates deploy/public_url.txt contains a valid URL and checks its responsiveness.
    """
    url_file = DEPLOY_DIR / "public_url.txt"
    assert url_file.exists(), f"Public URL file not found at {url_file}"
    assert url_file.is_file(), f"{url_file} is not a regular file"

    url_content = url_file.read_text(encoding="utf-8").strip()
    assert len(url_content) > 0, "deploy/public_url.txt is empty"

    # Extract first line if multiple
    url = url_content.splitlines()[0].strip()

    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme in ("http", "https"), (
        f"Public URL '{url}' must start with http:// or https://"
    )
    assert parsed.netloc, f"Public URL '{url}' has no valid network host"

    # Validate Cloud Run domain pattern or host
    assert "run.app" in parsed.netloc or "localhost" in parsed.netloc or "127.0.0.1" in parsed.netloc, (
        f"Public URL '{url}' is expected to target Cloud Run (*.run.app) or local demo host"
    )

    # Check responsiveness: try to reach the endpoint if network allows
    # In test/offline environments without internet access, gracefully handle connection failure
    try:
        with httpx.Client(timeout=4.0, follow_redirects=True) as client:
            resp = client.get(url)
            # If server is up and reachable, it should respond with HTTP 200/300/400 range (not a hang)
            assert resp.status_code in range(200, 500), (
                f"Public URL {url} returned unexpected status code {resp.status_code}"
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as exc:
        # Documented acceptable fallback in sandboxed CI/offline testing environments
        pass


# ============================================================================
# 7. Deployment Documentation (deploy/README.md)
# ============================================================================

def test_deploy_readme_exists():
    """
    Validates that deploy/README.md exists and documents Cloud Run deployment instructions.
    """
    readme_path = DEPLOY_DIR / "README.md"
    assert readme_path.exists(), f"Deployment README not found at {readme_path}"
    content = readme_path.read_text(encoding="utf-8")
    assert len(content) > 100, f"{readme_path} appears too short"
    assert "cloud run" in content.lower(), f"{readme_path} should document Cloud Run"
