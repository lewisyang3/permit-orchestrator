"""
Integration and unit tests for FastAPI Orchestrator Application (orchestrator.app).
"""

import asyncio
import json
from pathlib import Path
from typing import Dict
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource

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
from orchestrator.app import create_app
from orchestrator.stub_department import create_stub_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def sample_permit_payload() -> dict:
    fixture_path = Path("contract/fixtures/permit_approved.json")
    return json.loads(fixture_path.read_text())


def make_dept_summary(
    status: DepartmentStatus = DepartmentStatus.APPROVED,
    attempts: int = 1,
    latency_ms: float = 15.0,
    reasons: list = None,
    error_message: str = None,
) -> DepartmentResultSummary:
    if reasons is None:
        reasons = [
            EvaluationReason(
                rule_id="RULE-001",
                rule_name="Standard Check",
                passed=(status == DepartmentStatus.APPROVED),
                message="Review passed" if status == DepartmentStatus.APPROVED else "Rule violation",
            )
        ]
    return DepartmentResultSummary(
        status=status,
        attempts=attempts,
        latency_ms=latency_ms,
        error_message=error_message,
        reasons=reasons,
    )


def mock_approved_dispatcher(permit: Permit, trace_id: str, on_event=None) -> Dict[str, DepartmentResultSummary]:
    return {
        "zoning": make_dept_summary(DepartmentStatus.APPROVED),
        "fire": make_dept_summary(DepartmentStatus.APPROVED),
        "water": make_dept_summary(DepartmentStatus.APPROVED),
        "structural": make_dept_summary(DepartmentStatus.APPROVED),
    }


def test_health():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_cors_middleware():
    app = create_app()
    client = TestClient(app)
    resp = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") in ("*", "http://localhost:3000")


def test_submit_and_get_permit_approved(sample_permit_payload):
    app = create_app(dispatcher_override=mock_approved_dispatcher)
    client = TestClient(app)

    # 1. Submit permit - returns 202 Accepted
    submit_resp = client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    data = submit_resp.json()

    permit_id = data["permit_id"]
    assert permit_id == sample_permit_payload["permit_id"]
    assert data["state"] == SagaState.RECEIVED.value
    assert data["stream_url"] == f"/permits/{permit_id}/stream"
    UUID(data["trace_id"])

    # 2. Get permit status
    status_resp = client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["permit_id"] == permit_id
    assert status_data["trace_id"] == data["trace_id"]
    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.APPROVED.value
    assert status_data["decision_code"] == DecisionCode.ALL_DEPARTMENTS_APPROVED.value
    assert status_data["decided_at"] is not None
    assert len(status_data["department_results"]) == 4
    for dept in ["zoning", "fire", "water", "structural"]:
        assert dept in status_data["department_results"]
        assert status_data["department_results"][dept]["status"] == "APPROVED"


def test_submit_generates_uuid_when_permit_id_omitted(sample_permit_payload):
    app = create_app(dispatcher_override=mock_approved_dispatcher)
    client = TestClient(app)

    payload_no_id = sample_permit_payload.copy()
    payload_no_id.pop("permit_id", None)

    submit_resp = client.post("/submit?wait=true", json=payload_no_id)
    assert submit_resp.status_code == 202
    data = submit_resp.json()

    generated_id = data["permit_id"]
    UUID(generated_id)
    assert data["stream_url"] == f"/permits/{generated_id}/stream"

    status_resp = client.get(f"/permits/{generated_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["permit_id"] == generated_id
    assert status_resp.json()["state"] == SagaState.DECIDED.value


def test_submit_department_rejection(sample_permit_payload):
    def rejecting_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(DepartmentStatus.REJECTED),
            "water": make_dept_summary(DepartmentStatus.APPROVED),
            "structural": make_dept_summary(DepartmentStatus.APPROVED),
        }

    app = create_app(dispatcher_override=rejecting_dispatcher)
    client = TestClient(app)

    submit_resp = client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    status_resp = client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.REJECTED.value
    assert status_data["decision_code"] == DecisionCode.DEPARTMENT_REJECTIONS.value
    assert "fire" in status_data["decision_summary"].lower()


def test_submit_department_timeout_or_error(sample_permit_payload):
    def timeout_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(DepartmentStatus.TIMEOUT, error_message="Timeout after 3 attempts"),
            "water": make_dept_summary(DepartmentStatus.APPROVED),
            "structural": make_dept_summary(DepartmentStatus.APPROVED),
        }

    app = create_app(dispatcher_override=timeout_dispatcher)
    client = TestClient(app)

    submit_resp = client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    status_resp = client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.REJECTED.value
    assert status_data["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
    assert "fire" in status_data["decision_summary"].lower()


def test_submit_missing_department_result(sample_permit_payload):
    def missing_dept_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(DepartmentStatus.APPROVED),
            "water": make_dept_summary(DepartmentStatus.APPROVED),
        }

    app = create_app(dispatcher_override=missing_dept_dispatcher)
    client = TestClient(app)

    submit_resp = client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    status_resp = client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.REJECTED.value
    assert status_data["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
    assert "structural" in status_data["decision_summary"].lower()


def test_failsafe_saga_unhandled_exception(sample_permit_payload):
    """Test that an uncaught exception in the dispatcher fails safe to REJECTED/INCOMPLETE_REVIEWS."""
    def crashing_dispatcher(permit: Permit, trace_id: str, on_event=None):
        raise RuntimeError("Severe infrastructure failure")

    app = create_app(dispatcher_override=crashing_dispatcher)
    client = TestClient(app)

    submit_resp = client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    status_resp = client.get(f"/permits/{permit_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.REJECTED.value
    assert status_data["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
    assert "failed" in status_data["decision_summary"].lower()


def test_permit_not_found():
    app = create_app()
    client = TestClient(app)

    resp = client.get("/permits/non-existent-permit-id")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

    resp_stream = client.get("/permits/non-existent-permit-id/stream")
    assert resp_stream.status_code == 404


@pytest.mark.anyio
async def test_async_wait_false_and_await_completion(sample_permit_payload):
    async def delayed_dispatcher(permit: Permit, trace_id: str, on_event=None):
        await asyncio.sleep(0.05)
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(DepartmentStatus.APPROVED),
            "water": make_dept_summary(DepartmentStatus.APPROVED),
            "structural": make_dept_summary(DepartmentStatus.APPROVED),
        }

    app = create_app(dispatcher_override=delayed_dispatcher)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Submit with default wait=False
        submit_resp = await client.post("/submit", json=sample_permit_payload)
        assert submit_resp.status_code == 202
        data = submit_resp.json()
        permit_id = data["permit_id"]

        # Wait for completion via status endpoint with wait=true
        status_resp = await client.get(f"/permits/{permit_id}?wait=true")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["state"] == SagaState.DECIDED.value
        assert status_data["final_decision"] == FinalDecision.APPROVED.value


def test_permit_stream(sample_permit_payload):
    app = create_app(dispatcher_override=mock_approved_dispatcher)
    client = TestClient(app)

    submit_resp = client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    permit_id = submit_resp.json()["permit_id"]

    stream_resp = client.get(f"/permits/{permit_id}/stream")
    assert stream_resp.status_code == 200
    assert "text/event-stream" in stream_resp.headers["content-type"]
    assert stream_resp.headers.get("cache-control") == "no-cache"
    assert stream_resp.headers.get("connection") == "keep-alive"
    assert stream_resp.headers.get("x-accel-buffering") == "no"
    content = stream_resp.text
    assert "event: trace_event" in content
    assert "event: done" in content
    assert permit_id in content
    stream_resp.close()


@pytest.mark.anyio
async def test_integration_with_stub_department_apps(sample_permit_payload):
    """Integration test dispatching directly to stub department instances."""
    dept_apps = {
        name: create_stub_app(name)
        for name in ["zoning", "fire", "water", "structural"]
    }

    async def stub_direct_dispatcher(permit: Permit, trace_id: str, on_event=None) -> Dict[str, DepartmentResultSummary]:
        results = {}
        for dept_name, sub_app in dept_apps.items():
            transport = httpx.ASGITransport(app=sub_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
                req = DepartmentEvaluationRequest(
                    permit_id=permit.permit_id,
                    trace_id=trace_id,
                    department=DepartmentName(dept_name),
                    attempt=1,
                    permit=permit,
                )
                resp = await http_client.post("/evaluate", json=req.model_dump(mode="json"))
                eval_resp = DepartmentEvaluationResponse.model_validate(resp.json())
                results[dept_name] = DepartmentResultSummary(
                    status=eval_resp.status,
                    attempts=1,
                    latency_ms=eval_resp.execution_time_ms,
                    reasons=eval_resp.reasons,
                )
        return results

    app = create_app(dispatcher_override=stub_direct_dispatcher)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        submit_resp = await client.post("/submit?wait=true", json=sample_permit_payload)
        assert submit_resp.status_code == 202
        permit_id = submit_resp.json()["permit_id"]

        status_resp = await client.get(f"/permits/{permit_id}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["state"] == SagaState.DECIDED.value
        assert data["final_decision"] == FinalDecision.APPROVED.value
        assert data["decision_code"] == DecisionCode.ALL_DEPARTMENTS_APPROVED.value
        assert len(data["department_results"]) == 4


@pytest.mark.anyio
async def test_real_dispatcher_and_consensus_integration(sample_permit_payload, monkeypatch):
    """Integration test verifying orchestrator.app natively wired to orchestrator.dispatcher & consensus."""
    from orchestrator import dispatcher
    from orchestrator.stub_department import app as stub_app

    # Mount stub app via httpx transport for default endpoints
    transport = httpx.ASGITransport(app=stub_app)

    async def mock_dispatch(permit: Permit, trace_id: str, on_event=None):
        async with httpx.AsyncClient(transport=transport) as mock_http_client:
            return await dispatcher.dispatch_permit(
                permit=permit,
                trace_id=trace_id,
                endpoints={
                    "zoning": "http://testserver/evaluate",
                    "fire": "http://testserver/evaluate",
                    "water": "http://testserver/evaluate",
                    "structural": "http://testserver/evaluate",
                },
                client=mock_http_client,
                on_event=on_event,
            )

    app = create_app(dispatcher_override=mock_dispatch)
    app_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=app_transport, base_url="http://orchestrator") as client:
        sub = await client.post("/submit?wait=true", json=sample_permit_payload)
        assert sub.status_code == 202
        permit_id = sub.json()["permit_id"]

        stat = await client.get(f"/permits/{permit_id}")
        assert stat.status_code == 200
        res = stat.json()
        assert res["state"] == "DECIDED"
        assert res["final_decision"] == "APPROVED"
        assert res["decision_code"] == "ALL_DEPARTMENTS_APPROVED"
        assert len(res["department_results"]) == 4


def test_admin_fault_endpoints():
    app = create_app()
    client = TestClient(app)

    # 1. 422 Unprocessable Entity when department is missing
    missing_dept = client.post("/admin/fault", json={"mode": "slow", "delay_ms": 3000})
    assert missing_dept.status_code == 422
    assert "Department is required" in missing_dept.json()["detail"]

    # 2. GET /admin/fault returns AdminFaultResponse mapping for all 4 departments
    get_resp = client.get("/admin/fault")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert "zoning" in data
    assert "fire" in data
    assert "water" in data
    assert "structural" in data
    for dept_key in ("zoning", "fire", "water", "structural"):
        assert data[dept_key]["department"] == dept_key
        assert "mode" in data[dept_key]


@pytest.mark.anyio
async def test_in_flight_permit_status_schema_conformance(sample_permit_payload):
    """Verify in-flight permit status has all 4 departments and passes JSON Schema validation."""
    schemas_dir = Path("contract/schemas")
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text())))
        for p in schemas_dir.glob("*.json")
    ]
    registry = Registry().with_resources(resources)
    validator = Draft7Validator(
        json.loads((schemas_dir / "permit_status_response.schema.json").read_text()),
        registry=registry,
        format_checker=FormatChecker(),
    )

    unblock_event = asyncio.Event()

    async def blocking_dispatcher(permit: Permit, trace_id: str, on_event=None):
        await unblock_event.wait()
        return mock_approved_dispatcher(permit, trace_id, on_event=on_event)

    app = create_app(dispatcher_override=blocking_dispatcher)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        submit_resp = await client.post("/submit?wait=false", json=sample_permit_payload)
        assert submit_resp.status_code == 202
        permit_id = submit_resp.json()["permit_id"]

        # In-flight query
        status_resp = await client.get(f"/permits/{permit_id}?wait=false")
        assert status_resp.status_code == 200
        in_flight_data = status_resp.json()
        assert in_flight_data["state"] in (SagaState.RECEIVED.value, SagaState.EVALUATING.value)
        assert set(in_flight_data["department_results"].keys()) == {"zoning", "fire", "water", "structural"}
        for dept in ("zoning", "fire", "water", "structural"):
            assert in_flight_data["department_results"][dept]["status"] == "PENDING"
            assert in_flight_data["department_results"][dept]["attempts"] == 0

        # Validate against JSON schema
        validator.validate(in_flight_data)

        # Unblock and wait for completion
        unblock_event.set()
        done_resp = await client.get(f"/permits/{permit_id}?wait=true")
        assert done_resp.status_code == 200
        assert done_resp.json()["state"] == SagaState.DECIDED.value


@pytest.mark.anyio
async def test_background_task_lifecycle(sample_permit_payload):
    """Verify background task is tracked and discarded when finished."""
    unblock_event = asyncio.Event()

    async def blocking_dispatcher(permit: Permit, trace_id: str, on_event=None):
        await unblock_event.wait()
        return mock_approved_dispatcher(permit, trace_id, on_event=on_event)

    app = create_app(dispatcher_override=blocking_dispatcher)
    orchestrator = app.state.orchestrator
    assert len(orchestrator.background_tasks) == 0

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        submit_resp = await client.post("/submit?wait=false", json=sample_permit_payload)
        assert submit_resp.status_code == 202
        permit_id = submit_resp.json()["permit_id"]

        # Task should be in background_tasks
        assert len(orchestrator.background_tasks) == 1

        # Unblock and wait
        unblock_event.set()
        await client.get(f"/permits/{permit_id}?wait=true")
        await asyncio.sleep(0.01)
        assert len(orchestrator.background_tasks) == 0


@pytest.mark.anyio
async def test_sse_stream_deduplication(sample_permit_payload):
    """Verify SSE streaming deduplicates trace events replay vs live subscriber queues."""
    from datetime import datetime, timezone
    from contract.models import PermitStatusResponse, SpanId, TraceEvent, TraceEventType

    app = create_app(dispatcher_override=mock_approved_dispatcher)
    orchestrator = app.state.orchestrator
    permit_id = "test-stream-dedup-001"

    initial_dept_results = {
        dept.value: DepartmentResultSummary(
            status=DepartmentStatus.PENDING,
            attempts=0,
            latency_ms=0.0,
            reasons=[],
        )
        for dept in DepartmentName
    }
    orchestrator.permits_db[permit_id] = PermitStatusResponse(
        permit_id=permit_id,
        trace_id="test-trace",
        state=SagaState.EVALUATING,
        final_decision=FinalDecision.PENDING,
        decision_code=DecisionCode.PENDING,
        created_at=datetime.now(timezone.utc),
        department_results=initial_dept_results,
    )

    ev1 = TraceEvent(
        event_id="evt-dup-1",
        trace_id="test-trace",
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        parent_span=None,
        type=TraceEventType.PERMIT_RECEIVED,
        ts=datetime.now(timezone.utc),
        detail={},
    )
    # Put event in past events
    orchestrator.trace_events[permit_id] = [ev1]

    # Also push the SAME event to the subscriber after queue connects, then None
    async def feed_events():
        await asyncio.sleep(0.05)
        orchestrator.record_event(permit_id, ev1)
        orchestrator.permits_db[permit_id].state = SagaState.DECIDED
        orchestrator.permits_db[permit_id].final_decision = FinalDecision.APPROVED
        orchestrator.close_stream_subscribers(permit_id)

    task = asyncio.create_task(feed_events())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/permits/{permit_id}/stream")
        assert resp.status_code == 200
        text = resp.text
        # Count occurrences of "evt-dup-1" in SSE output - must be exactly 1
        assert text.count("evt-dup-1") == 1
        assert "event: done" in text

    await task


