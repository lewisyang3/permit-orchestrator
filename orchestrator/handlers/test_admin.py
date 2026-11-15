"""
Tests for Admin Fault Forwarding Handlers (orchestrator/handlers/admin.py).
"""

from __future__ import annotations

import asyncio
from typing import Dict

import httpx
import pytest
from fastapi import FastAPI, status

from contract.models import (
    AdminFaultResponse,
    DepartmentName,
    FaultControlRequest,
    FaultControlResponse,
    FaultMode,
)
from orchestrator.handlers.admin import (
    ALL_DEPARTMENTS,
    get_http_client,
    resolve_fault_url,
    router as admin_router,
)
from orchestrator.stub_department import create_stub_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


class DepartmentRouterTransport(httpx.AsyncBaseTransport):
    """Routes HTTP requests to the appropriate stub department ASGI application by hostname."""

    def __init__(self, dept_apps: Dict[str, FastAPI]):
        self.transports = {
            name: httpx.ASGITransport(app=app) for name, app in dept_apps.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.transports:
            return await self.transports[host].handle_async_request(request)
        raise httpx.ConnectError(f"Connection refused to host {host}")


@pytest.fixture
def stub_departments():
    """Create independent stub department apps for all four departments."""
    return {
        "zoning": create_stub_app("zoning"),
        "fire": create_stub_app("fire"),
        "water": create_stub_app("water"),
        "structural": create_stub_app("structural"),
    }


@pytest.fixture
def app_with_stubs(stub_departments):
    """Create test FastAPI application with admin router and configured stub client."""
    app = FastAPI(title="Test Admin App")
    app.include_router(admin_router)

    transport = DepartmentRouterTransport(stub_departments)
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    app.state.http_client = client
    return app


@pytest.mark.anyio
async def test_resolve_fault_url():
    """Verify that resolve_fault_url handles various input URL formats correctly."""
    assert resolve_fault_url("http://fire:8002") == "http://fire:8002/fault"
    assert resolve_fault_url("http://fire:8002/") == "http://fire:8002/fault"
    assert resolve_fault_url("http://fire:8002/evaluate") == "http://fire:8002/fault"
    assert resolve_fault_url("http://fire:8002/evaluate/") == "http://fire:8002/fault"
    assert (
        resolve_fault_url("http://custom.dept.internal:9000/subpath/evaluate")
        == "http://custom.dept.internal:9000/subpath/fault"
    )


@pytest.mark.anyio
async def test_post_admin_fault_success(app_with_stubs):
    """Verify POST /admin/fault successfully forwards fault config to target department."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        # 1. Set Fire to SLOW
        req_fire = FaultControlRequest(
            department=DepartmentName.FIRE,
            mode=FaultMode.SLOW,
            delay_ms=3500,
        )
        resp = await client.post("/admin/fault", json=req_fire.model_dump(mode="json"))
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        fc_resp = FaultControlResponse.model_validate(data)
        assert fc_resp.department == DepartmentName.FIRE
        assert fc_resp.mode == FaultMode.SLOW
        assert fc_resp.delay_ms == 3500

        # 2. Set Water to ERROR
        req_water = FaultControlRequest(
            department=DepartmentName.WATER,
            mode=FaultMode.ERROR,
            error_code=503,
            error_message="Water main burst",
        )
        resp2 = await client.post("/admin/fault", json=req_water.model_dump(mode="json"))
        assert resp2.status_code == status.HTTP_200_OK
        data2 = resp2.json()
        fc_resp2 = FaultControlResponse.model_validate(data2)
        assert fc_resp2.department == DepartmentName.WATER
        assert fc_resp2.mode == FaultMode.ERROR

        # 3. Verify GET /admin/fault reflects the updated fault states (status reflection)
        get_resp = await client.get("/admin/fault")
        assert get_resp.status_code == status.HTTP_200_OK
        admin_resp = AdminFaultResponse.model_validate(get_resp.json())
        assert admin_resp.fire.mode == FaultMode.SLOW
        assert admin_resp.fire.delay_ms == 3500
        assert admin_resp.water.mode == FaultMode.ERROR
        assert admin_resp.zoning.mode == FaultMode.NONE
        assert admin_resp.structural.mode == FaultMode.NONE


@pytest.mark.anyio
async def test_post_admin_fault_validation_errors(app_with_stubs):
    """Verify validation when department is missing or empty."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        # Missing department
        payload = {"mode": "slow", "delay_ms": 3000}
        resp = await client.post("/admin/fault", json=payload)
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Department is required" in resp.json()["detail"]

        # Null department
        payload_null = {"department": None, "mode": "none"}
        resp_null = await client.post("/admin/fault", json=payload_null)
        assert resp_null.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Department is required" in resp_null.json()["detail"]


@pytest.mark.anyio
async def test_post_admin_fault_unknown_department():
    """Verify error handling when department endpoint cannot be resolved."""
    app = FastAPI()
    app.include_router(admin_router)
    # Set endpoints missing fire
    app.state.department_endpoints = {
        "zoning": "http://zoning:8001",
        # fire missing
        "water": "http://water:8003",
        "structural": "http://structural:8004",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        req = FaultControlRequest(
            department=DepartmentName.FIRE,
            mode=FaultMode.OFFLINE,
        )
        resp = await client.post("/admin/fault", json=req.model_dump(mode="json"))
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Unknown department 'fire'" in resp.json()["detail"]


@pytest.mark.anyio
async def test_post_admin_fault_upstream_error():
    """Verify handling when department service returns an HTTP status error."""
    app = FastAPI()
    app.include_router(admin_router)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal department error")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        req = FaultControlRequest(
            department=DepartmentName.ZONING,
            mode=FaultMode.ERROR,
        )
        resp = await client.post("/admin/fault", json=req.model_dump(mode="json"))
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Department fault injection failed" in resp.json()["detail"]


@pytest.mark.anyio
async def test_post_admin_fault_network_failure():
    """Verify HTTP 502 Bad Gateway when department service is unreachable on POST."""
    app = FastAPI()
    app.include_router(admin_router)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        req = FaultControlRequest(
            department=DepartmentName.ZONING,
            mode=FaultMode.OFFLINE,
        )
        resp = await client.post("/admin/fault", json=req.model_dump(mode="json"))
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Failed to communicate with department service" in resp.json()["detail"]


@pytest.mark.anyio
async def test_get_admin_fault_all_departments_ok(app_with_stubs):
    """Verify GET /admin/fault returns status for all 4 departments."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        resp = await client.get("/admin/fault")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()

        for dept_name in ["zoning", "fire", "water", "structural"]:
            assert dept_name in data
            assert data[dept_name]["department"] == dept_name
            assert data[dept_name]["mode"] == "none"

        admin_resp = AdminFaultResponse.model_validate(data)
        assert admin_resp.zoning.mode == FaultMode.NONE
        assert admin_resp.fire.mode == FaultMode.NONE
        assert admin_resp.water.mode == FaultMode.NONE
        assert admin_resp.structural.mode == FaultMode.NONE


@pytest.mark.anyio
async def test_get_admin_fault_with_offline_departments():
    """Verify GET /admin/fault handles partial department outages gracefully without failing."""
    app = FastAPI()
    app.include_router(admin_router)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "fire":
            # Fire service completely offline
            raise httpx.ConnectError("Connection refused to fire service")
        elif host == "water":
            # Water service returns 502 / bad gateway
            return httpx.Response(502, text="Bad Gateway")
        else:
            # Zoning and structural are healthy
            return httpx.Response(
                200,
                json={"department": host, "mode": "none", "delay_ms": 3000, "message": "OK"},
            )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/admin/fault")
        assert resp.status_code == status.HTTP_200_OK
        admin_resp = AdminFaultResponse.model_validate(resp.json())

        # Zoning and structural should be healthy
        assert admin_resp.zoning.mode == FaultMode.NONE
        assert admin_resp.structural.mode == FaultMode.NONE

        # Fire should gracefully report offline
        assert admin_resp.fire.mode == FaultMode.NONE
        assert "Offline / Unreachable" in admin_resp.fire.message

        # Water should report HTTP 502
        assert admin_resp.water.mode == FaultMode.NONE
        assert "HTTP 502" in admin_resp.water.message


@pytest.mark.anyio
async def test_get_admin_fault_unconfigured_endpoint():
    """Verify GET /admin/fault when an endpoint is missing from endpoints mapping."""
    app = FastAPI()
    app.include_router(admin_router)
    app.state.department_endpoints = {
        "zoning": "http://zoning:8001",
        # fire missing
        "water": "http://water:8003",
        "structural": "http://structural:8004",
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"department": request.url.host, "mode": "none", "delay_ms": 3000, "message": "OK"},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/admin/fault")
        assert resp.status_code == status.HTTP_200_OK
        admin_resp = AdminFaultResponse.model_validate(resp.json())
        assert admin_resp.fire.message == "Endpoint unconfigured"


@pytest.mark.anyio
async def test_admin_fault_reset_to_none(app_with_stubs):
    """Verify fault injection can switch to SLOW/OFFLINE and then reset back to NONE via POST /admin/fault."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        # Step 1: Set structural to OFFLINE
        req_offline = FaultControlRequest(
            department=DepartmentName.STRUCTURAL,
            mode=FaultMode.OFFLINE,
            delay_ms=2500,
        )
        resp_set = await client.post("/admin/fault", json=req_offline.model_dump(mode="json"))
        assert resp_set.status_code == status.HTTP_200_OK
        assert resp_set.json()["mode"] == "offline"

        # Verify GET reflects OFFLINE
        resp_get = await client.get("/admin/fault")
        assert resp_get.status_code == status.HTTP_200_OK
        data = resp_get.json()
        assert data["structural"]["mode"] == "offline"

        # Step 2: Reset structural to NONE
        req_reset = FaultControlRequest(
            department=DepartmentName.STRUCTURAL,
            mode=FaultMode.NONE,
        )
        resp_reset = await client.post("/admin/fault", json=req_reset.model_dump(mode="json"))
        assert resp_reset.status_code == status.HTTP_200_OK
        assert resp_reset.json()["mode"] == "none"

        # Verify GET reflects NONE (status reflection)
        resp_get2 = await client.get("/admin/fault")
        assert resp_get2.status_code == status.HTTP_200_OK
        assert resp_get2.json()["structural"]["mode"] == "none"


@pytest.mark.anyio
async def test_post_admin_reset_all_departments(app_with_stubs):
    """Verify POST /admin/reset resets all departments back to NONE and reflects immediately."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        # 1. Inject faults into multiple departments
        await client.post(
            "/admin/fault",
            json=FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.SLOW, delay_ms=4000).model_dump(
                mode="json"
            ),
        )
        await client.post(
            "/admin/fault",
            json=FaultControlRequest(
                department=DepartmentName.WATER, mode=FaultMode.ERROR, error_code=500
            ).model_dump(mode="json"),
        )
        await client.post(
            "/admin/fault",
            json=FaultControlRequest(department=DepartmentName.STRUCTURAL, mode=FaultMode.OFFLINE).model_dump(
                mode="json"
            ),
        )

        # Verify before reset
        pre_get = await client.get("/admin/fault")
        pre_data = pre_get.json()
        assert pre_data["fire"]["mode"] == "slow"
        assert pre_data["water"]["mode"] == "error"
        assert pre_data["structural"]["mode"] == "offline"

        # 2. Call POST /admin/reset with no body
        reset_resp = await client.post("/admin/reset")
        assert reset_resp.status_code == status.HTTP_200_OK
        reset_data = reset_resp.json()
        admin_resp = AdminFaultResponse.model_validate(reset_data)

        assert admin_resp.zoning.mode == FaultMode.NONE
        assert admin_resp.fire.mode == FaultMode.NONE
        assert admin_resp.water.mode == FaultMode.NONE
        assert admin_resp.structural.mode == FaultMode.NONE

        # 3. Status reflection check: GET /admin/fault reflects reset
        post_get = await client.get("/admin/fault")
        assert post_get.status_code == status.HTTP_200_OK
        post_data = post_get.json()
        assert post_data["fire"]["mode"] == "none"
        assert post_data["water"]["mode"] == "none"
        assert post_data["structural"]["mode"] == "none"
        assert post_data["zoning"]["mode"] == "none"


@pytest.mark.anyio
async def test_post_admin_reset_with_empty_json_and_all_flag(app_with_stubs):
    """Verify POST /admin/reset with empty JSON and query parameter department=all."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        # Set fire to slow
        await client.post(
            "/admin/fault",
            json=FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.SLOW).model_dump(mode="json"),
        )

        # Reset with empty JSON payload {}
        resp1 = await client.post("/admin/reset", json={})
        assert resp1.status_code == status.HTTP_200_OK
        assert resp1.json()["fire"]["mode"] == "none"

        # Set water to error
        await client.post(
            "/admin/fault",
            json=FaultControlRequest(department=DepartmentName.WATER, mode=FaultMode.ERROR).model_dump(mode="json"),
        )

        # Reset with query parameter ?department=all
        resp2 = await client.post("/admin/reset?department=all")
        assert resp2.status_code == status.HTTP_200_OK
        assert resp2.json()["water"]["mode"] == "none"


@pytest.mark.anyio
async def test_post_admin_reset_specific_department(app_with_stubs):
    """Verify POST /admin/reset with specific department only resets that department."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        # Set Fire to SLOW and Water to ERROR
        await client.post(
            "/admin/fault",
            json=FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.SLOW, delay_ms=3000).model_dump(
                mode="json"
            ),
        )
        await client.post(
            "/admin/fault",
            json=FaultControlRequest(
                department=DepartmentName.WATER, mode=FaultMode.ERROR, error_code=500
            ).model_dump(mode="json"),
        )

        # Reset only FIRE via body
        reset_fire = await client.post(
            "/admin/reset",
            json=FaultControlRequest(department=DepartmentName.FIRE).model_dump(mode="json"),
        )
        assert reset_fire.status_code == status.HTTP_200_OK
        data = reset_fire.json()
        assert data["fire"]["mode"] == "none"
        assert data["water"]["mode"] == "error"

        # Reset WATER via query param
        reset_water = await client.post("/admin/reset?department=water")
        assert reset_water.status_code == status.HTTP_200_OK
        data2 = reset_water.json()
        assert data2["water"]["mode"] == "none"
        assert data2["fire"]["mode"] == "none"


@pytest.mark.anyio
async def test_post_admin_reset_unknown_department():
    """Verify POST /admin/reset returns 404 when target department is unknown or unconfigured."""
    app = FastAPI()
    app.include_router(admin_router)
    app.state.department_endpoints = {
        "zoning": "http://zoning:8001",
        # water missing
        "fire": "http://fire:8002",
        "structural": "http://structural:8004",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Unconfigured department
        resp = await client.post(
            "/admin/reset",
            json=FaultControlRequest(department=DepartmentName.WATER).model_dump(mode="json"),
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "Unknown department 'water'" in resp.json()["detail"]

        # Invalid department query param
        resp_invalid = await client.post("/admin/reset?department=nonexistent")
        assert resp_invalid.status_code == status.HTTP_404_NOT_FOUND
        assert "Unknown department 'nonexistent'" in resp_invalid.json()["detail"]


@pytest.mark.anyio
async def test_post_admin_reset_specific_department_errors():
    """Verify error propagation when resetting a specific target department."""
    app = FastAPI()
    app.include_router(admin_router)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "fire":
            raise httpx.ConnectError("Connection refused")
        elif host == "water":
            return httpx.Response(500, text="Internal reset error")
        return httpx.Response(200, json={"department": host, "mode": "none"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Network error on target department
        resp_fire = await client.post(
            "/admin/reset",
            json=FaultControlRequest(department=DepartmentName.FIRE).model_dump(mode="json"),
        )
        assert resp_fire.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Failed to communicate with department service" in resp_fire.json()["detail"]

        # 500 error on target department
        resp_water = await client.post(
            "/admin/reset",
            json=FaultControlRequest(department=DepartmentName.WATER).model_dump(mode="json"),
        )
        assert resp_water.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Department fault reset failed" in resp_water.json()["detail"]


@pytest.mark.anyio
async def test_post_admin_reset_all_with_offline_department():
    """Verify POST /admin/reset all departments handles partial outages gracefully without 500."""
    app = FastAPI()
    app.include_router(admin_router)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "fire":
            raise httpx.ConnectError("Connection refused to fire")
        return httpx.Response(200, json={"department": host, "mode": "none", "delay_ms": 3000})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/admin/reset")
        assert resp.status_code == status.HTTP_200_OK
        admin_resp = AdminFaultResponse.model_validate(resp.json())

        assert admin_resp.zoning.mode == FaultMode.NONE
        assert admin_resp.structural.mode == FaultMode.NONE
        assert admin_resp.water.mode == FaultMode.NONE
        assert "Offline / Unreachable" in admin_resp.fire.message


@pytest.mark.anyio
async def test_unprefixed_routes(app_with_stubs):
    """Verify /fault and /reset un-prefixed routes work identically to /admin/fault and /admin/reset."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_stubs),
        base_url="http://test",
    ) as client:
        # POST /fault
        req = FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.SLOW, delay_ms=2000)
        resp1 = await client.post("/fault", json=req.model_dump(mode="json"))
        assert resp1.status_code == status.HTTP_200_OK
        assert resp1.json()["mode"] == "slow"

        # GET /fault
        resp2 = await client.get("/fault")
        assert resp2.status_code == status.HTTP_200_OK
        assert resp2.json()["fire"]["mode"] == "slow"

        # POST /reset
        resp3 = await client.post("/reset")
        assert resp3.status_code == status.HTTP_200_OK
        assert resp3.json()["fire"]["mode"] == "none"


@pytest.mark.anyio
async def test_admin_fault_prefix_mounting(stub_departments):
    """Verify compatibility when admin router is mounted with prefix='/admin'."""
    app = FastAPI()
    app.include_router(admin_router, prefix="/admin")

    transport = DepartmentRouterTransport(stub_departments)
    app.state.http_client = httpx.AsyncClient(transport=transport, timeout=5.0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # POST /admin/fault should work
        req = FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.SLOW, delay_ms=4000)
        post_resp = await client.post("/admin/fault", json=req.model_dump(mode="json"))
        assert post_resp.status_code == status.HTTP_200_OK
        assert post_resp.json()["mode"] == "slow"

        # GET /admin/fault should work
        get_resp = await client.get("/admin/fault")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["fire"]["mode"] == "slow"

        # POST /admin/reset should work
        reset_resp = await client.post("/admin/reset")
        assert reset_resp.status_code == status.HTTP_200_OK
        assert reset_resp.json()["fire"]["mode"] == "none"


def test_admin_fault_with_sync_testclient(app_with_stubs):
    """Verify compatibility with FastAPI TestClient (sync test execution)."""
    from fastapi.testclient import TestClient

    client = TestClient(app_with_stubs)
    # GET /admin/fault
    resp = client.get("/admin/fault")
    assert resp.status_code == 200
    data = resp.json()
    for dept in ("zoning", "fire", "water", "structural"):
        assert dept in data
        assert "mode" in data[dept]

    # POST /admin/fault
    post_resp = client.post("/admin/fault", json={"department": "fire", "mode": "slow", "delay_ms": 3000})
    assert post_resp.status_code == 200
    assert post_resp.json()["mode"] == "slow"

    # POST /admin/reset
    reset_resp = client.post("/admin/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["fire"]["mode"] == "none"


@pytest.mark.anyio
async def test_post_admin_fault_malformed_department_response():
    """Verify handling when department returns non-JSON or non-dict response on POST."""
    app = FastAPI()
    app.include_router(admin_router)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not a json dict")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        req = FaultControlRequest(department=DepartmentName.ZONING, mode=FaultMode.NONE)
        resp = await client.post("/admin/fault", json=req.model_dump(mode="json"))
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Failed to communicate with department service" in resp.json()["detail"]


@pytest.mark.anyio
async def test_get_admin_fault_malformed_department_response():
    """Verify GET /admin/fault handles non-dict JSON responses gracefully without failing."""
    app = FastAPI()
    app.include_router(admin_router)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "zoning":
            return httpx.Response(200, json=["unexpected", "list"])
        return httpx.Response(200, json={"department": host, "mode": "none"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    app.state.http_client = mock_client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/admin/fault")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "Invalid JSON response" in data["zoning"]["message"]
        assert data["fire"]["mode"] == "none"
