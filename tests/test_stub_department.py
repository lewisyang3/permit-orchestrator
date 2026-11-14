"""Unit tests for orchestrator/stub_department.py verifying /evaluate and all fault modes."""

import json
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from contract.models import (
    DepartmentName,
    DepartmentStatus,
    FaultMode,
)
from orchestrator.stub_department import app, create_stub_app


@pytest.fixture
def sample_permit():
    return json.loads(Path("contract/fixtures/permit_approved.json").read_text())


@pytest.fixture
def client():
    # Fresh stub app per test to ensure clean state
    fresh_app = create_stub_app()
    return TestClient(fresh_app)


def test_health_endpoint(client):
    """GET /health returns {'status': 'healthy'}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.parametrize("dept", [
    DepartmentName.ZONING,
    DepartmentName.FIRE,
    DepartmentName.WATER,
    DepartmentName.STRUCTURAL,
])
def test_evaluate_all_departments_approved(client, sample_permit, dept):
    """POST /evaluate returns DepartmentEvaluationResponse with status=APPROVED for any department."""
    payload = {
        "permit_id": "test-permit-001",
        "trace_id": "test-trace-001",
        "department": dept.value,
        "attempt": 1,
        "permit": sample_permit,
    }
    response = client.post("/evaluate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["permit_id"] == "test-permit-001"
    assert data["department"] == dept.value
    assert data["status"] == DepartmentStatus.APPROVED.value
    assert isinstance(data["execution_time_ms"], (int, float))
    assert len(data["reasons"]) == 1
    reason = data["reasons"][0]
    assert reason["rule_id"] == "STUB-001"
    assert reason["rule_name"] == "Stub Check"
    assert reason["passed"] is True
    assert reason["message"] == "Stub passed"
    assert "evaluated_at" in data


def test_fault_mode_offline(client, sample_permit):
    """Fault mode offline causes POST /evaluate to return HTTP 503."""
    set_fault = client.post("/fault", json={"mode": "offline"})
    assert set_fault.status_code == 200
    assert set_fault.json()["mode"] == "offline"

    # Evaluate should return 503
    payload = {
        "permit_id": "test-permit-offline",
        "trace_id": "test-trace-offline",
        "department": "fire",
        "attempt": 1,
        "permit": sample_permit,
    }
    response = client.post("/evaluate", json=payload)
    assert response.status_code == 503

    # Reset fault mode to none
    reset_fault = client.post("/fault", json={"mode": "none"})
    assert reset_fault.status_code == 200
    assert reset_fault.json()["mode"] == "none"

    # Evaluate should now succeed
    recovery_resp = client.post("/evaluate", json=payload)
    assert recovery_resp.status_code == 200
    assert recovery_resp.json()["status"] == DepartmentStatus.APPROVED.value


def test_fault_mode_error(client, sample_permit):
    """Fault mode error causes POST /evaluate to return HTTP 500 (or custom error_code)."""
    # Default 500
    client.post("/fault", json={"mode": "error", "error_code": 500, "error_message": "Custom test error"})
    payload = {
        "permit_id": "test-permit-err",
        "trace_id": "test-trace-err",
        "department": "water",
        "attempt": 1,
        "permit": sample_permit,
    }
    response = client.post("/evaluate", json=payload)
    assert response.status_code == 500
    assert response.json()["detail"] == "Custom test error"

    # Custom 502 Bad Gateway
    client.post("/fault", json={"mode": "error", "error_code": 502, "error_message": "Gateway error"})
    resp_502 = client.post("/evaluate", json=payload)
    assert resp_502.status_code == 502
    assert resp_502.json()["detail"] == "Gateway error"


def test_fault_mode_slow(client, sample_permit):
    """Fault mode slow causes POST /evaluate to sleep delay_ms and return APPROVED."""
    client.post("/fault", json={"mode": "slow", "delay_ms": 60})
    payload = {
        "permit_id": "test-permit-slow",
        "trace_id": "test-trace-slow",
        "department": "zoning",
        "attempt": 1,
        "permit": sample_permit,
    }
    start = time.perf_counter()
    response = client.post("/evaluate", json=payload)
    elapsed = (time.perf_counter() - start) * 1000.0

    assert response.status_code == 200
    assert response.json()["status"] == DepartmentStatus.APPROVED.value
    # Ensure delay was respected
    assert elapsed >= 50.0
    assert response.json()["execution_time_ms"] >= 50.0


def test_department_targeted_fault(client, sample_permit):
    """Fault targeted at a single department does not affect other departments."""
    client.post("/fault", json={"department": "fire", "mode": "offline"})

    # Fire should fail with 503
    fire_payload = {
        "permit_id": "test-fire",
        "trace_id": "test-trace",
        "department": "fire",
        "attempt": 1,
        "permit": sample_permit,
    }
    assert client.post("/evaluate", json=fire_payload).status_code == 503

    # Structural should succeed normally
    structural_payload = {
        "permit_id": "test-structural",
        "trace_id": "test-trace",
        "department": "structural",
        "attempt": 1,
        "permit": sample_permit,
    }
    resp_struct = client.post("/evaluate", json=structural_payload)
    assert resp_struct.status_code == 200
    assert resp_struct.json()["status"] == DepartmentStatus.APPROVED.value


def test_get_fault(client):
    """GET /fault returns current fault control settings."""
    # Initially none
    res = client.get("/fault")
    assert res.status_code == 200
    assert res.json()["mode"] == "none"

    # After setting fire to slow
    client.post("/fault", json={"department": "fire", "mode": "slow", "delay_ms": 2500})
    res_fire = client.get("/fault?department=fire")
    assert res_fire.status_code == 200
    data_fire = res_fire.json()
    assert data_fire["department"] == "fire"
    assert data_fire["mode"] == "slow"
    assert data_fire["delay_ms"] == 2500


def test_department_specific_app_instance(sample_permit):
    """create_stub_app with a specific department enforces department match."""
    fire_app = create_stub_app("fire")
    with TestClient(fire_app) as fire_client:
        # Matching department succeeds
        resp = fire_client.post("/evaluate", json={
            "permit_id": "p-1",
            "trace_id": "t-1",
            "department": "fire",
            "attempt": 1,
            "permit": sample_permit,
        })
        assert resp.status_code == 200
        assert resp.json()["department"] == "fire"

        # Mismatched department returns 422
        mismatch_resp = fire_client.post("/evaluate", json={
            "permit_id": "p-2",
            "trace_id": "t-2",
            "department": "zoning",
            "attempt": 1,
            "permit": sample_permit,
        })
        assert mismatch_resp.status_code == 422


def test_default_app_import():
    """Ensure module-level app and router can be imported and executed."""
    with TestClient(app) as default_client:
        assert default_client.get("/health").status_code == 200
