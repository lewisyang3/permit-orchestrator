"""Comprehensive tests for Task T4: Department microservice fault modes.

Verifies:
1. Fault control endpoint GET /fault and POST /fault across all 4 departments.
2. Modes: 'none', 'offline' (HTTP 503), 'slow' (sleeps delay_ms), 'error' (HTTP 500/custom).
3. One-call switching into any fault mode and immediate clean recovery back to 'none'.
4. Correct accounting of delay_ms in execution_time_ms.
5. GET /health remains 200 OK and non-blocking regardless of active fault mode.
6. Department matching validation on /fault (HTTP 422 on mismatch).
7. Negative delay_ms rejection (HTTP 422).
8. Strict schema conformance with contract/schemas/fault_control.schema.json.
9. Compatibility with contract/models.py FaultControlRequest and FaultControlResponse.
10. Full integration with orchestrator.dispatcher (retries, timeouts, and consensus).
"""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any, Dict

import httpx
from jsonschema import Draft7Validator, FormatChecker
import pytest
from fastapi.testclient import TestClient
from referencing import Registry, Resource

from contract.models import (
    DecisionCode,
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    FaultControlRequest,
    FaultControlResponse,
    FaultMode,
    FinalDecision,
    Permit,
)
from departments.app import create_app as department_app
from orchestrator.consensus import evaluate_consensus
from orchestrator.dispatcher import dispatch_permit

SCHEMAS_DIR = Path("contract/schemas")
FIXTURES_DIR = Path("contract/fixtures")


def _get_fault_schema_validator() -> Draft7Validator:
    resources = []
    for p in SCHEMAS_DIR.glob("*.json"):
        schema = json.loads(p.read_text())
        resources.append((p.name, Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    fault_schema = json.loads((SCHEMAS_DIR / "fault_control.schema.json").read_text())
    return Draft7Validator(fault_schema, registry=registry, format_checker=FormatChecker())


@pytest.fixture
def fault_validator():
    return _get_fault_schema_validator()


@pytest.fixture
def sample_permit() -> Permit:
    raw = json.loads((FIXTURES_DIR / "permit_approved.json").read_text())
    return Permit.model_validate(raw)


@pytest.fixture
def fire_client():
    with TestClient(department_app("fire")) as client:
        yield client


@pytest.mark.parametrize("dept", [
    DepartmentName.ZONING,
    DepartmentName.FIRE,
    DepartmentName.WATER,
    DepartmentName.STRUCTURAL,
])
def test_initial_fault_state_and_schema_conformance(dept, fault_validator):
    """GET /fault returns mode=none and validates against JSON schema and Pydantic models."""
    with TestClient(department_app(dept.value)) as client:
        resp = client.get("/fault")
        assert resp.status_code == 200
        data = resp.json()

        assert data["department"] == dept.value
        assert data["mode"] == FaultMode.NONE.value
        assert data["delay_ms"] == 3000
        assert data["error_code"] == 500
        assert "message" in data

        # Conforms to fault_control.schema.json
        errors = list(fault_validator.iter_errors(data))
        assert not errors, f"Schema errors: {errors}"

        # Validates against contract models
        req_model = FaultControlRequest.model_validate(data)
        assert req_model.mode == FaultMode.NONE
        resp_model = FaultControlResponse.model_validate(data)
        assert resp_model.mode == FaultMode.NONE
        assert resp_model.department == dept


@pytest.mark.parametrize("dept", [
    DepartmentName.ZONING,
    DepartmentName.FIRE,
    DepartmentName.WATER,
    DepartmentName.STRUCTURAL,
])
def test_health_remains_healthy_under_all_fault_modes(dept):
    """GET /health stays fast and 200 OK across offline, slow, and error fault modes."""
    with TestClient(department_app(dept.value)) as client:
        for mode in [FaultMode.NONE, FaultMode.OFFLINE, FaultMode.SLOW, FaultMode.ERROR]:
            set_resp = client.post("/fault", json={"mode": mode.value, "delay_ms": 2000})
            assert set_resp.status_code == 200

            # Health check must respond immediately without being slowed down or errored
            start = perf_counter()
            health_resp = client.get("/health")
            duration = perf_counter() - start

            assert health_resp.status_code == 200
            assert health_resp.json() == {"status": "healthy", "department": dept.value}
            assert duration < 0.2, f"Health check took too long ({duration:.3f}s) under mode {mode}"


def test_get_fault_query_parameter_validation(fire_client):
    """GET /fault?department=... validates target department match."""
    # Matching query succeeds
    resp_match = fire_client.get("/fault?department=fire")
    assert resp_match.status_code == 200
    assert resp_match.json()["department"] == "fire"

    # Mismatched query returns 422
    resp_mismatch = fire_client.get("/fault?department=water")
    assert resp_mismatch.status_code == 422
    assert "water" in resp_mismatch.json()["detail"]


def test_post_fault_target_department_validation(fire_client):
    """POST /fault validates that department if specified matches service department."""
    # Matching department succeeds
    resp_ok = fire_client.post("/fault", json={"department": "fire", "mode": "none"})
    assert resp_ok.status_code == 200

    # Omitting department defaults to service department
    resp_none = fire_client.post("/fault", json={"mode": "none"})
    assert resp_none.status_code == 200
    assert resp_none.json()["department"] == "fire"

    # Mismatched department returns 422
    resp_err = fire_client.post("/fault", json={"department": "structural", "mode": "offline"})
    assert resp_err.status_code == 422
    assert "structural" in resp_err.json()["detail"]


def test_post_fault_negative_delay_rejected(fire_client):
    """POST /fault rejects negative delay_ms with HTTP 422."""
    resp = fire_client.post("/fault", json={"mode": "slow", "delay_ms": -500})
    assert resp.status_code == 422
    assert "delay_ms" in resp.json()["detail"]


def test_fault_mode_offline_and_recovery(fire_client, sample_permit, fault_validator):
    """POST /fault mode=offline causes evaluate to return 503; switching back to none recovers."""
    eval_payload = {
        "permit_id": "pm-test-offline-001",
        "trace_id": "trc-001",
        "department": "fire",
        "attempt": 1,
        "permit": sample_permit.model_dump(mode="json"),
    }

    # 1. Switch to offline
    fault_resp = fire_client.post("/fault", json={"mode": "offline"})
    assert fault_resp.status_code == 200
    assert fault_resp.json()["mode"] == "offline"
    assert not list(fault_validator.iter_errors(fault_resp.json()))

    # 2. Evaluate must return 503 Service Unavailable
    eval_resp = fire_client.post("/evaluate", json=eval_payload)
    assert eval_resp.status_code == 503
    assert "offline" in eval_resp.json()["detail"].lower()

    # 3. Clean recovery back to none
    recover_resp = fire_client.post("/fault", json={"mode": "none"})
    assert recover_resp.status_code == 200
    assert recover_resp.json()["mode"] == "none"

    # 4. Evaluate is no longer 503 (executes rules)
    eval_recovered = fire_client.post("/evaluate", json=eval_payload)
    assert eval_recovered.status_code != 503


def test_fault_mode_error_default_and_custom(fire_client, sample_permit, fault_validator):
    """POST /fault mode=error raises HTTP 500 by default or custom error_code with message."""
    eval_payload = {
        "permit_id": "pm-test-err-001",
        "trace_id": "trc-002",
        "department": "fire",
        "attempt": 1,
        "permit": sample_permit.model_dump(mode="json"),
    }

    # 1. Default error: 500
    resp_500 = fire_client.post("/fault", json={"mode": "error"})
    assert resp_500.status_code == 200
    assert resp_500.json()["error_code"] == 500

    eval_500 = fire_client.post("/evaluate", json=eval_payload)
    assert eval_500.status_code == 500
    assert eval_500.json()["detail"] == "Simulated fault injection"

    # 2. Custom error: 502 Bad Gateway
    resp_502 = fire_client.post(
        "/fault",
        json={"mode": "error", "error_code": 502, "error_message": "Upstream Fire DB failure"},
    )
    assert resp_502.status_code == 200
    assert resp_502.json()["error_code"] == 502

    eval_502 = fire_client.post("/evaluate", json=eval_payload)
    assert eval_502.status_code == 502
    assert eval_502.json()["detail"] == "Upstream Fire DB failure"

    # 3. Reset
    fire_client.post("/fault", json={"mode": "none"})
    assert fire_client.post("/evaluate", json=eval_payload).status_code not in (500, 502)


def test_fault_mode_slow_delays_execution_and_reports_accurate_latency(tmp_path, sample_permit):
    """POST /fault mode=slow sleeps delay_ms and execution_time_ms reflects the delay."""
    # Configure an approval-allowing test rule pack to inspect execution_time_ms in 200 response
    import yaml
    approval_pack = {
        "schema_version": "0.2.0",
        "rule_set_id": "test-fire-approval",
        "version": "0.2.0",
        "department": "fire",
        "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": True},
        "rules": [
            {
                "rule_id": "TEST-F-PASS",
                "rule_name": "Hydrant distance limit",
                "applies_when": [],
                "requirement": {
                    "cases": [
                        {
                            "when": [],
                            "checks": [
                                {
                                    "field": "distance_to_hydrant_ft",
                                    "operator": "lte",
                                    "value": 1000.0,
                                    "unit": "ft",
                                }
                            ],
                        }
                    ]
                },
            }
        ],
    }
    rule_file = tmp_path / "fire_rules.yaml"
    rule_file.write_text(yaml.dump(approval_pack))

    with TestClient(department_app("fire", rule_file=rule_file)) as client:
        # Inject slow mode with 70ms delay
        delay_target_ms = 70
        client.post("/fault", json={"mode": "slow", "delay_ms": delay_target_ms})

        eval_payload = {
            "permit_id": "pm-test-slow-001",
            "trace_id": "trc-003",
            "department": "fire",
            "attempt": 1,
            "permit": sample_permit.model_dump(mode="json"),
        }

        wall_start = perf_counter()
        resp = client.post("/evaluate", json=eval_payload)
        wall_duration_ms = (perf_counter() - wall_start) * 1000.0

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == DepartmentStatus.APPROVED.value

        # Wall clock and execution_time_ms must both reflect the injected delay
        assert wall_duration_ms >= 60.0
        assert data["execution_time_ms"] >= 60.0, (
            f"execution_time_ms ({data['execution_time_ms']}) did not account for delay_ms ({delay_target_ms})"
        )


def test_one_call_switch_and_recovery_done_criterion(fire_client, sample_permit):
    """Validates the explicit 'Done when' criterion: One call switches into a fault mode and back."""
    eval_payload = {
        "permit_id": "pm-test-switch-001",
        "trace_id": "trc-004",
        "department": "fire",
        "attempt": 1,
        "permit": sample_permit.model_dump(mode="json"),
    }

    # Step 1: Baseline none
    fire_client.post("/fault", json={"mode": "none"})
    assert fire_client.get("/fault").json()["mode"] == "none"

    # Step 2: One call switches to offline
    r1 = fire_client.post("/fault", json={"mode": "offline"})
    assert r1.status_code == 200
    assert fire_client.post("/evaluate", json=eval_payload).status_code == 503

    # Step 3: One call switches back to none
    r2 = fire_client.post("/fault", json={"mode": "none"})
    assert r2.status_code == 200
    assert fire_client.post("/evaluate", json=eval_payload).status_code != 503

    # Step 4: One call switches to error
    r3 = fire_client.post("/fault", json={"mode": "error", "error_code": 500})
    assert r3.status_code == 200
    assert fire_client.post("/evaluate", json=eval_payload).status_code == 500

    # Step 5: One call switches back to none
    r4 = fire_client.post("/fault", json={"mode": "none"})
    assert r4.status_code == 200
    assert fire_client.post("/evaluate", json=eval_payload).status_code not in (500, 503)


@pytest.mark.anyio
async def test_orchestrator_dispatcher_integration_with_real_department_fault(sample_permit):
    """
    Integration test: Orchestrator dispatcher fan-out against real department services.
    Verifies that when Fire is set to OFFLINE:
    1. Dispatcher retries Fire 3 times with backoff.
    2. Fire status ends as ERROR (HTTP 503).
    3. Consensus rules result in REJECTED with INCOMPLETE_REVIEWS code.
    4. Recovering Fire to none allows healthy dispatch and consensus.
    """
    # Create TestClient instances to trigger FastAPI startup lifespans for all 4 real departments
    apps = {
        "zoning": department_app("zoning"),
        "fire": department_app("fire"),
        "water": department_app("water"),
        "structural": department_app("structural"),
    }
    test_clients = {name: TestClient(app) for name, app in apps.items()}
    for tc in test_clients.values():
        tc.__enter__()

    try:
        class RealDeptTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                host = request.url.host
                if host not in test_clients:
                    raise RuntimeError(f"Unknown target host {host}")
                tc = test_clients[host]
                method = request.method
                path = request.url.raw_path.decode("ascii")
                content = await request.aread()

                if method.upper() == "GET":
                    resp = tc.get(path)
                elif method.upper() == "POST":
                    resp = tc.post(path, content=content, headers=dict(request.headers))
                else:
                    resp = tc.request(method, path, content=content)

                return httpx.Response(
                    status_code=resp.status_code,
                    content=resp.content,
                    headers=resp.headers,
                    request=request,
                )

        endpoints = {
            "zoning": "http://zoning/evaluate",
            "fire": "http://fire/evaluate",
            "water": "http://water/evaluate",
            "structural": "http://structural/evaluate",
        }

        async with httpx.AsyncClient(transport=RealDeptTransport()) as client:
            # 1. Baseline: Inject OFFLINE into Fire via POST http://fire/fault
            resp_fault = await client.post("http://fire/fault", json={"mode": "offline"})
            assert resp_fault.status_code == 200
            assert resp_fault.json()["mode"] == "offline"

            # 2. Dispatch permit with fast backoff
            trace_events = []
            results = await dispatch_permit(
                permit=sample_permit,
                trace_id="trc-fault-test",
                endpoints=endpoints,
                client=client,
                backoff_delays=[0.02, 0.04],
                on_event=lambda ev: trace_events.append(ev),
            )

            # 3. Assert Fire failed after 3 attempts with ERROR
            fire_res = results["fire"]
            assert fire_res.status == DepartmentStatus.ERROR
            assert fire_res.attempts == 3
            assert "503" in (fire_res.error_message or "")

            # 4. Assert Consensus rule: INCOMPLETE_REVIEWS -> REJECTED
            final_dec, dec_code, summary, violations = evaluate_consensus(results)
            assert final_dec == FinalDecision.REJECTED
            assert dec_code == DecisionCode.INCOMPLETE_REVIEWS
            assert "fire" in summary.lower()

            # 5. Clear Fire fault back to none
            clear_resp = await client.post("http://fire/fault", json={"mode": "none"})
            assert clear_resp.status_code == 200
            assert clear_resp.json()["mode"] == "none"

            # 6. Verify evaluate is no longer 503
            eval_resp = await client.post(
                "http://fire/evaluate",
                json={
                    "permit_id": "pm-clear-001",
                    "trace_id": "trc-clear",
                    "department": "fire",
                    "attempt": 1,
                    "permit": sample_permit.model_dump(mode="json"),
                },
            )
            assert eval_resp.status_code != 503
    finally:
        for tc in test_clients.values():
            tc.__exit__(None, None, None)

