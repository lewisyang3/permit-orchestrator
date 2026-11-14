"""
End-to-end integration verification tests for Task T1 (Orchestrator Core).

Tests the full orchestrator pipeline:
- FastAPI orchestrator app (orchestrator.app)
- Parallel fan-out dispatcher (orchestrator.dispatcher) with timeouts and retries
- Consensus engine (orchestrator.consensus)
- Four stub departments (orchestrator.stub_department: zoning, fire, water, structural)
"""

import json
from pathlib import Path
from typing import Dict

import httpx
import pytest

from contract.models import (
    DecisionCode,
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    FaultControlRequest,
    FaultMode,
    FinalDecision,
    Permit,
    SagaState,
)
from orchestrator import dispatcher
from orchestrator.app import create_app
from orchestrator.stub_department import create_stub_app


class DepartmentRouterTransport(httpx.AsyncBaseTransport):
    """Routes HTTP requests to the appropriate stub department ASGI application by hostname."""

    def __init__(self, dept_apps: Dict[str, httpx.ASGITransport]):
        self.transports = {
            name: httpx.ASGITransport(app=app) for name, app in dept_apps.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.transports:
            return await self.transports[host].handle_async_request(request)
        raise RuntimeError(f"Unknown target department host: {host}")


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
def sample_permit_payload() -> dict:
    fixture_path = Path("contract/fixtures/permit_approved.json")
    return json.loads(fixture_path.read_text())


@pytest.fixture
def sample_rejected_permit_payload() -> dict:
    fixture_path = Path("contract/fixtures/permit_rejected_fire.json")
    return json.loads(fixture_path.read_text())


@pytest.fixture
async def e2e_harness(stub_departments):
    """
    Creates an end-to-end test harness:
    - 4 stub department apps (zoning, fire, water, structural)
    - DepartmentRouterTransport routing requests to them
    - Orchestrator app configured with dispatcher calling the router transport
    - HTTP async clients for orchestrator and individual departments
    """
    shared_client = httpx.AsyncClient(
        transport=DepartmentRouterTransport(stub_departments)
    )

    async def e2e_dispatcher(permit: Permit, trace_id: str) -> Dict[str, DepartmentResultSummary]:
        return await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            client=shared_client,
            backoff_delays=[0.05, 0.1],  # Accelerated backoff for fast test execution
        )

    orch_app = create_app(dispatcher_override=e2e_dispatcher)
    orch_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=orch_app), base_url="http://orchestrator"
    )

    dept_clients = {
        name: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"http://{name}"
        )
        for name, app in stub_departments.items()
    }

    try:
        yield {
            "app": orch_app,
            "client": orch_client,
            "departments": stub_departments,
            "dept_clients": dept_clients,
            "shared_client": shared_client,
        }
    finally:
        await orch_client.aclose()
        for dc in dept_clients.values():
            await dc.aclose()
        await shared_client.aclose()


@pytest.mark.anyio
async def test_e2e_approved_permit_all_four_departments(e2e_harness, sample_permit_payload):
    """
    Validates T1 'Done when' criteria:
    Submitting a sample permit returns the expected APPROVED decision from all four stub departments.
    """
    client = e2e_harness["client"]

    # 1. Verify orchestrator health endpoint
    health_resp = await client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "healthy"}

    # 2. Submit sample permit via POST /submit
    submit_resp = await client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    submit_data = submit_resp.json()

    permit_id = submit_data["permit_id"]
    trace_id = submit_data["trace_id"]
    assert permit_id == sample_permit_payload["permit_id"]
    assert submit_data["state"] == SagaState.RECEIVED.value
    assert submit_data["stream_url"] == f"/permits/{permit_id}/stream"

    # 3. Query permit status via GET /permits/{permit_id}
    status_resp = await client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["permit_id"] == permit_id
    assert status_data["trace_id"] == trace_id
    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.APPROVED.value
    assert status_data["decision_code"] == DecisionCode.ALL_DEPARTMENTS_APPROVED.value
    assert "approved" in status_data["decision_summary"].lower()

    dept_results = status_data["department_results"]
    assert len(dept_results) == 4
    for dept_name in ["zoning", "fire", "water", "structural"]:
        assert dept_name in dept_results
        result = dept_results[dept_name]
        assert result["status"] == DepartmentStatus.APPROVED.value
        assert result["attempts"] == 1
        assert result["latency_ms"] >= 0
        assert len(result["reasons"]) >= 1
        assert result["reasons"][0]["passed"] is True
        assert result["reasons"][0]["rule_id"] == "STUB-001"


@pytest.mark.anyio
async def test_e2e_fault_injection_offline_department(e2e_harness, sample_permit_payload):
    """
    Validates fail-safe consensus rule when a department service is taken offline:
    Injecting OFFLINE mode on Fire department causes 503 errors, exercises 3 retries,
    and returns REJECTED with INCOMPLETE_REVIEWS.
    """
    client = e2e_harness["client"]
    fire_client = e2e_harness["dept_clients"]["fire"]

    # 1. Inject OFFLINE fault on Fire department
    fault_cmd = FaultControlRequest(
        department=DepartmentName.FIRE,
        mode=FaultMode.OFFLINE,
    )
    fault_resp = await fire_client.post("/fault", json=fault_cmd.model_dump(mode="json"))
    assert fault_resp.status_code == 200

    # Verify fault status on Fire department
    get_fault_resp = await fire_client.get("/fault?department=fire")
    assert get_fault_resp.status_code == 200
    assert get_fault_resp.json()["mode"] == FaultMode.OFFLINE.value

    # 2. Submit permit
    submit_resp = await client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    # 3. Query status
    status_resp = await client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.REJECTED.value
    assert status_data["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
    assert "fire" in status_data["decision_summary"].lower()

    # Verify Fire department result summary recorded ERROR after 3 attempts
    dept_results = status_data["department_results"]
    fire_result = dept_results["fire"]
    assert fire_result["status"] == DepartmentStatus.ERROR.value
    assert fire_result["attempts"] == 3
    assert "503" in fire_result["error_message"]

    # Verify other 3 healthy departments were not blocked and succeeded
    for healthy_dept in ["zoning", "water", "structural"]:
        assert dept_results[healthy_dept]["status"] == DepartmentStatus.APPROVED.value
        assert dept_results[healthy_dept]["attempts"] == 1


@pytest.mark.anyio
async def test_e2e_fault_injection_slow_department_times_out(e2e_harness, sample_permit_payload):
    """
    Validates per-call timeout enforcement and retries:
    Injecting SLOW mode on Fire department past the timeout limit causes ClientTimeout,
    exercises retries with backoff, and results in REJECTED / INCOMPLETE_REVIEWS.
    """
    client = e2e_harness["client"]
    fire_client = e2e_harness["dept_clients"]["fire"]

    # Configure custom dispatcher with short timeout for fast and deterministic test execution
    shared_client = e2e_harness["shared_client"]

    async def fast_timeout_dispatcher(permit: Permit, trace_id: str):
        return await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            client=shared_client,
            timeout_seconds=0.1,  # 100ms timeout
            backoff_delays=[0.05, 0.05],
        )

    e2e_harness["app"].state.orchestrator.custom_dispatcher = fast_timeout_dispatcher

    # Set Fire department to sleep 250ms (exceeding 100ms timeout)
    fault_cmd = FaultControlRequest(
        department=DepartmentName.FIRE,
        mode=FaultMode.SLOW,
        delay_ms=250,
    )
    await fire_client.post("/fault", json=fault_cmd.model_dump(mode="json"))

    # Submit permit
    submit_resp = await client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    status_resp = await client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.REJECTED.value
    assert status_data["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
    assert "fire" in status_data["decision_summary"].lower()

    fire_result = status_data["department_results"]["fire"]
    assert fire_result["status"] == DepartmentStatus.TIMEOUT.value
    assert fire_result["attempts"] == 3
    assert "Timeout" in fire_result["error_message"]


@pytest.mark.anyio
async def test_e2e_department_rejection_consensus(e2e_harness, sample_rejected_permit_payload):
    """
    Validates consensus rule 2 (Department Rejections):
    When all 4 departments respond and at least one rejects,
    the final decision is REJECTED with DEPARTMENT_REJECTIONS code and aggregated violations.
    """
    client = e2e_harness["client"]

    # Configure a custom router where fire department returns REJECTED for non-compliant permit
    async def rejecting_dispatcher(permit: Permit, trace_id: str):
        results = await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            client=e2e_harness["shared_client"],
            backoff_delays=[0.05, 0.05],
        )
        # Simulate Fire department rejecting due to missing sprinkler system in assembly occupancy
        results["fire"] = DepartmentResultSummary(
            status=DepartmentStatus.REJECTED,
            attempts=1,
            latency_ms=22.0,
            reasons=[
                EvaluationReason(
                    rule_id="FIRE-002",
                    rule_name="Sprinkler Requirement for Assembly",
                    passed=False,
                    message="Occupancy load exceeds 100 without required sprinkler system.",
                )
            ],
            error_message=None,
        )
        return results

    e2e_harness["app"].state.orchestrator.custom_dispatcher = rejecting_dispatcher

    submit_resp = await client.post("/submit?wait=true", json=sample_rejected_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    status_resp = await client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.REJECTED.value
    assert status_data["decision_code"] == DecisionCode.DEPARTMENT_REJECTIONS.value
    assert "fire" in status_data["decision_summary"].lower()

    dept_results = status_data["department_results"]
    assert dept_results["fire"]["status"] == DepartmentStatus.REJECTED.value
    assert len(dept_results["fire"]["reasons"]) == 1
    assert dept_results["fire"]["reasons"][0]["passed"] is False
    assert dept_results["fire"]["reasons"][0]["rule_id"] == "FIRE-002"


@pytest.mark.anyio
async def test_e2e_fault_recovery_cycle(e2e_harness, sample_permit_payload):
    """
    Validates end-to-end recovery:
    1. Initial permit is APPROVED.
    2. Fault is injected (Fire OFFLINE) -> Next permit is REJECTED.
    3. Fault is cleared (Fire NONE) -> Subsequent permit is APPROVED again.
    """
    client = e2e_harness["client"]
    fire_client = e2e_harness["dept_clients"]["fire"]

    # 1. Baseline: Approved
    resp1 = await client.post("/submit?wait=true", json=sample_permit_payload)
    assert resp1.status_code == 202
    stat1 = (await client.get(f"/permits/{resp1.json()['permit_id']}")).json()
    assert stat1["final_decision"] == FinalDecision.APPROVED.value

    # 2. Inject fault: Offline
    await fire_client.post(
        "/fault",
        json=FaultControlRequest(
            department=DepartmentName.FIRE, mode=FaultMode.OFFLINE
        ).model_dump(mode="json"),
    )
    resp2 = await client.post("/submit?wait=true", json=sample_permit_payload)
    assert resp2.status_code == 202
    stat2 = (await client.get(f"/permits/{resp2.json()['permit_id']}")).json()
    assert stat2["final_decision"] == FinalDecision.REJECTED.value
    assert stat2["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value

    # 3. Clear fault: None
    await fire_client.post(
        "/fault",
        json=FaultControlRequest(
            department=DepartmentName.FIRE, mode=FaultMode.NONE
        ).model_dump(mode="json"),
    )
    resp3 = await client.post("/submit?wait=true", json=sample_permit_payload)
    assert resp3.status_code == 202
    stat3 = (await client.get(f"/permits/{resp3.json()['permit_id']}")).json()
    assert stat3["final_decision"] == FinalDecision.APPROVED.value
    assert stat3["decision_code"] == DecisionCode.ALL_DEPARTMENTS_APPROVED.value
