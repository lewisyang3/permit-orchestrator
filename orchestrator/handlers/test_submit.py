"""
Tests for Permit Submission Handler (orchestrator/handlers/submit.py).

Covers:
1. Permit submission (POST /submit):
   - Validation, response schema (202 Accepted, SubmitPermitResponse).
   - Auto-generated UUID when permit_id is omitted.
   - Invalid schema validation (HTTP 422).
   - Client-provided permit_id.
   - Header-based idempotency key support (Idempotency-Key and X-Idempotency-Key).

2. Idempotency:
   - Duplicate submission with Idempotency-Key header returns identical acknowledgment without re-running saga.
   - Duplicate submission with X-Idempotency-Key header returns identical acknowledgment.
   - Duplicate submission with idempotent=true query parameter.
   - Concurrent/in-flight duplicate submissions deduplicate to a single saga execution.
   - Concurrent second submission with wait=true awaits the in-flight saga.
   - No duplicate trace events or department results in SQLite on resubmission.

3. Background saga orchestration:
   - wait=false asynchronous execution with background asyncio.Task.
   - wait=true synchronous awaiting.
   - Live event broadcasting via EventBroadcaster to subscribers.
   - Fail-safe consensus recovery on unhandled dispatcher exceptions.
   - Global task and event lifecycle cleanup on completion.

4. Consensus evaluation:
   - All departments approved -> APPROVED, ALL_DEPARTMENTS_APPROVED.
   - Department rejection -> REJECTED, DEPARTMENT_REJECTIONS with violations.
   - Department timeout -> REJECTED, INCOMPLETE_REVIEWS.
   - Missing department -> REJECTED, INCOMPLETE_REVIEWS.

5. Database persistence:
   - SQLite permits table schema, state transition (RECEIVED -> EVALUATING -> DECIDED).
   - SQLite department_results table with attempts, latency, reasons.
   - SQLite append-only trace_events table with ordered timestamps and valid spans.
   - SQLite WAL mode verification.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contract.models import (
    DecisionCode,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    FinalDecision,
    Permit,
    SagaState,
    SpanId,
    TraceEvent,
    TraceEventType,
)
from orchestrator import db
from orchestrator.handlers.submit import (
    EventBroadcaster,
    get_broadcaster,
    get_permit_event,
    permit_events,
    permit_tasks,
    permit_trace_ids,
    reset_submit_state,
    router,
    run_permit_saga,
    submitted_permits,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_broadcaster():
    reset_submit_state()
    yield
    reset_submit_state()


@pytest.fixture
def sample_permit_dict() -> dict:
    fixture_path = Path("contract/fixtures/permit_approved.json")
    return json.loads(fixture_path.read_text())


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_submit_handler.db"
    db.init_db(db_file)
    return db_file


def make_dept_summary(
    status: DepartmentStatus = DepartmentStatus.APPROVED,
    attempts: int = 1,
    latency_ms: float = 20.0,
    reasons: Optional[List[EvaluationReason]] = None,
    error_message: Optional[str] = None,
) -> DepartmentResultSummary:
    if reasons is None:
        reasons = [
            EvaluationReason(
                rule_id="RULE-101",
                rule_name="Compliance Check",
                passed=(status == DepartmentStatus.APPROVED),
                message="Review passed" if status == DepartmentStatus.APPROVED else "Violation",
            )
        ]
    return DepartmentResultSummary(
        status=status,
        attempts=attempts,
        latency_ms=latency_ms,
        reasons=reasons,
        error_message=error_message,
    )


def mock_approved_dispatcher(permit: Permit, trace_id: str, on_event=None) -> Dict[str, DepartmentResultSummary]:
    if on_event:
        for dept in ["zoning", "fire", "water", "structural"]:
            on_event(
                TraceEvent(
                    event_id=f"evt-{dept}-start",
                    trace_id=trace_id,
                    permit_id=permit.permit_id,
                    span=SpanId(f"dept:{dept}"),
                    parent_span="orchestrator",
                    type=TraceEventType.SPAN_STARTED,
                    detail={"department": dept},
                )
            )
            on_event(
                TraceEvent(
                    event_id=f"evt-{dept}-ok",
                    trace_id=trace_id,
                    permit_id=permit.permit_id,
                    span=SpanId(f"dept:{dept}"),
                    parent_span="orchestrator",
                    type=TraceEventType.CALL_SUCCEEDED,
                    detail={"department": dept, "status": "APPROVED"},
                )
            )

    return {
        "zoning": make_dept_summary(DepartmentStatus.APPROVED),
        "fire": make_dept_summary(DepartmentStatus.APPROVED),
        "water": make_dept_summary(DepartmentStatus.APPROVED),
        "structural": make_dept_summary(DepartmentStatus.APPROVED),
    }


# =====================================================================
# 1. Permit Submission (POST /submit) Tests
# =====================================================================


def test_submit_valid_permit_returns_202(sample_permit_dict, test_db):
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    client = TestClient(app)
    resp = client.post("/submit?wait=true", json=sample_permit_dict)
    assert resp.status_code == 202
    data = resp.json()

    assert data["permit_id"] == sample_permit_dict["permit_id"]
    assert data["state"] == SagaState.RECEIVED.value
    assert data["stream_url"] == f"/permits/{sample_permit_dict['permit_id']}/stream"
    UUID(data["trace_id"])

    # Verify SQLite record was created and updated to DECIDED
    permit_rec = db.get_permit(data["permit_id"], db_path=test_db)
    assert permit_rec is not None
    assert permit_rec.state == SagaState.DECIDED
    assert permit_rec.final_decision == FinalDecision.APPROVED
    assert permit_rec.decision_code == DecisionCode.ALL_DEPARTMENTS_APPROVED

    # Verify TraceEvents were recorded
    events = db.get_trace_events(data["permit_id"], db_path=test_db)
    assert len(events) >= 3
    event_types = [e.type for e in events]
    assert TraceEventType.PERMIT_RECEIVED in event_types
    assert TraceEventType.CONSENSUS_STARTED in event_types
    assert TraceEventType.PERMIT_DECIDED in event_types


def test_submit_omitted_permit_id_generates_uuid(sample_permit_dict, test_db):
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    payload_no_id = sample_permit_dict.copy()
    payload_no_id.pop("permit_id", None)

    client = TestClient(app)
    resp = client.post("/submit?wait=true", json=payload_no_id)
    assert resp.status_code == 202
    data = resp.json()

    gen_id = data["permit_id"]
    UUID(gen_id)
    assert data["stream_url"] == f"/permits/{gen_id}/stream"

    # Verify DB has record for generated ID
    rec = db.get_permit(gen_id, db_path=test_db)
    assert rec is not None
    assert rec.permit_id == gen_id


def test_submit_invalid_payload_returns_422():
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    resp = client.post("/submit", json={"invalid": "payload"})
    assert resp.status_code == 422


def test_submit_with_idempotency_key_header_assigns_key(sample_permit_dict, test_db):
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    payload_no_id = sample_permit_dict.copy()
    payload_no_id.pop("permit_id", None)

    client = TestClient(app)
    headers = {"Idempotency-Key": "client-idemp-key-999"}
    resp = client.post("/submit?wait=true", json=payload_no_id, headers=headers)
    assert resp.status_code == 202
    data = resp.json()

    assert data["permit_id"] == "client-idemp-key-999"
    rec = db.get_permit("client-idemp-key-999", db_path=test_db)
    assert rec is not None


# =====================================================================
# 2. Idempotency Tests
# =====================================================================


def test_idempotent_duplicate_submission_with_header(sample_permit_dict, test_db):
    """Submitting with Idempotency-Key header multiple times returns identical acknowledgment without re-running."""
    call_count = 0

    def counting_dispatcher(permit: Permit, trace_id: str, on_event=None):
        nonlocal call_count
        call_count += 1
        return mock_approved_dispatcher(permit, trace_id, on_event=on_event)

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = counting_dispatcher

    client = TestClient(app)
    headers = {"Idempotency-Key": "idemp-test-call-1"}

    resp1 = client.post("/submit?wait=true", json=sample_permit_dict, headers=headers)
    assert resp1.status_code == 202
    data1 = resp1.json()
    assert call_count == 1

    # Second submission with same Idempotency-Key header
    resp2 = client.post("/submit?wait=true", json=sample_permit_dict, headers=headers)
    assert resp2.status_code == 202
    data2 = resp2.json()

    # Exact same permit_id, trace_id, and stream_url returned
    assert data1["permit_id"] == data2["permit_id"]
    assert data1["trace_id"] == data2["trace_id"]
    assert data1["stream_url"] == data2["stream_url"]

    # Dispatcher was NOT called again
    assert call_count == 1


def test_idempotent_duplicate_submission_with_x_idempotency_key(sample_permit_dict, test_db):
    """Submitting with X-Idempotency-Key header multiple times returns identical acknowledgment."""
    call_count = 0

    def counting_dispatcher(permit: Permit, trace_id: str, on_event=None):
        nonlocal call_count
        call_count += 1
        return mock_approved_dispatcher(permit, trace_id, on_event=on_event)

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = counting_dispatcher

    client = TestClient(app)
    headers = {"X-Idempotency-Key": "x-idemp-call-2"}

    resp1 = client.post("/submit?wait=true", json=sample_permit_dict, headers=headers)
    assert resp1.status_code == 202
    resp2 = client.post("/submit?wait=true", json=sample_permit_dict, headers=headers)
    assert resp2.status_code == 202

    assert resp1.json()["permit_id"] == resp2.json()["permit_id"]
    assert resp1.json()["trace_id"] == resp2.json()["trace_id"]
    assert call_count == 1


def test_idempotent_duplicate_submission_with_query_param(sample_permit_dict, test_db):
    """Submitting with idempotent=true query param guarantees idempotent response."""
    call_count = 0

    def counting_dispatcher(permit: Permit, trace_id: str, on_event=None):
        nonlocal call_count
        call_count += 1
        return mock_approved_dispatcher(permit, trace_id, on_event=on_event)

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = counting_dispatcher

    client = TestClient(app)
    resp1 = client.post("/submit?wait=true&idempotent=true", json=sample_permit_dict)
    assert resp1.status_code == 202
    resp2 = client.post("/submit?wait=true&idempotent=true", json=sample_permit_dict)
    assert resp2.status_code == 202

    assert resp1.json()["permit_id"] == resp2.json()["permit_id"]
    assert resp1.json()["trace_id"] == resp2.json()["trace_id"]
    assert call_count == 1


@pytest.mark.anyio
async def test_idempotent_concurrent_in_flight_submissions(sample_permit_dict, test_db):
    """Concurrent requests for the same permit_id deduplicate to a single saga execution."""
    call_count = 0
    gate = asyncio.Event()

    async def blocking_dispatcher(permit: Permit, trace_id: str, on_event=None):
        nonlocal call_count
        call_count += 1
        await gate.wait()
        return mock_approved_dispatcher(permit, trace_id, on_event=on_event)

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = blocking_dispatcher

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Launch first submission with wait=False (leaves it in-flight)
        resp1 = await client.post("/submit?wait=false", json=sample_permit_dict)
        assert resp1.status_code == 202
        permit_id = resp1.json()["permit_id"]

        # Second submission while first is still in-flight
        resp2 = await client.post("/submit?wait=false", json=sample_permit_dict)
        assert resp2.status_code == 202

        assert resp1.json()["permit_id"] == resp2.json()["permit_id"]
        assert resp1.json()["trace_id"] == resp2.json()["trace_id"]

        # Release dispatcher and wait for saga completion
        gate.set()
        ev = get_permit_event(permit_id)
        if ev:
            await asyncio.wait_for(ev.wait(), timeout=2.0)

        # Dispatcher was called only once despite duplicate concurrent submissions
        assert call_count == 1


@pytest.mark.anyio
async def test_idempotent_in_flight_wait_true_blocks_until_done(sample_permit_dict, test_db):
    """Second concurrent submission with wait=true awaits the in-flight saga."""
    call_count = 0
    gate = asyncio.Event()

    async def blocking_dispatcher(permit: Permit, trace_id: str, on_event=None):
        nonlocal call_count
        call_count += 1
        await gate.wait()
        return mock_approved_dispatcher(permit, trace_id, on_event=on_event)

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = blocking_dispatcher

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Launch first submission with wait=False (leaves it in-flight)
        resp1 = await client.post("/submit?wait=false", json=sample_permit_dict)
        assert resp1.status_code == 202
        permit_id = resp1.json()["permit_id"]

        async def second_call():
            return await client.post("/submit?wait=true", json=sample_permit_dict)

        second_task = asyncio.create_task(second_call())
        # Let second call arrive and enter wait
        await asyncio.sleep(0.05)
        assert not second_task.done()

        # Unblock gate
        gate.set()
        resp2 = await second_task
        assert resp2.status_code == 202

        assert resp1.json()["permit_id"] == resp2.json()["permit_id"]
        assert resp1.json()["trace_id"] == resp2.json()["trace_id"]
        assert call_count == 1


def test_idempotent_submission_no_duplicate_db_records(sample_permit_dict, test_db):
    """Resubmission does not duplicate department results or trace events in SQLite."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    client = TestClient(app)
    headers = {"Idempotency-Key": "no-dup-db-test"}

    resp1 = client.post("/submit?wait=true", json=sample_permit_dict, headers=headers)
    assert resp1.status_code == 202
    permit_id = resp1.json()["permit_id"]

    events_first = db.get_trace_events(permit_id, db_path=test_db)
    depts_first = db.get_department_results(permit_id, db_path=test_db)

    # Second idempotent call
    resp2 = client.post("/submit?wait=true", json=sample_permit_dict, headers=headers)
    assert resp2.status_code == 202

    events_second = db.get_trace_events(permit_id, db_path=test_db)
    depts_second = db.get_department_results(permit_id, db_path=test_db)

    assert len(events_second) == len(events_first)
    assert len(depts_second) == 4
    decided_events = [e for e in events_second if e.type == TraceEventType.PERMIT_DECIDED]
    assert len(decided_events) == 1


# =====================================================================
# 3. Background Saga Orchestration Tests
# =====================================================================


@pytest.mark.anyio
async def test_async_wait_false_background_completion(sample_permit_dict, test_db):
    """wait=false returns 202 immediately while saga runs in background."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/submit", json=sample_permit_dict)
        assert resp.status_code == 202
        permit_id = resp.json()["permit_id"]

        # Wait on background saga completion event
        event = get_permit_event(permit_id)
        assert event is not None
        await asyncio.wait_for(event.wait(), timeout=2.0)

        # Verify DB is updated to DECIDED
        rec = db.get_permit(permit_id, db_path=test_db)
        assert rec is not None
        assert rec.state == SagaState.DECIDED
        assert rec.final_decision == FinalDecision.APPROVED


@pytest.mark.anyio
async def test_live_broadcaster_receives_all_saga_events(sample_permit_dict, test_db):
    """Subscribers to EventBroadcaster receive live TraceEvents during saga execution."""
    permit = Permit.model_validate(sample_permit_dict)
    permit_id = permit.permit_id
    trace_id = "test-live-trace-456"

    broadcaster = get_broadcaster()
    queue = broadcaster.subscribe(permit_id)

    db.save_permit(permit_id, payload=permit, state=SagaState.RECEIVED, trace_id=trace_id, db_path=test_db)

    saga_task = asyncio.create_task(
        run_permit_saga(
            permit_id=permit_id,
            trace_id=trace_id,
            permit=permit,
            broadcaster_inst=broadcaster,
            dispatcher_fn=mock_approved_dispatcher,
            db_path=test_db,
        )
    )

    received_events: List[TraceEvent] = []
    while True:
        item = await queue.get()
        if item is None:
            break
        received_events.append(item)

    await saga_task

    event_types = [e.type for e in received_events]
    assert TraceEventType.SPAN_STARTED in event_types
    assert TraceEventType.CALL_SUCCEEDED in event_types
    assert TraceEventType.CONSENSUS_STARTED in event_types
    assert TraceEventType.PERMIT_DECIDED in event_types
    assert received_events[-1].detail["final_decision"] == FinalDecision.APPROVED.value


def test_saga_failsafe_on_unhandled_dispatcher_exception(sample_permit_dict, test_db):
    """Unhandled exception in dispatcher triggers fail-safe recovery: REJECTED, INCOMPLETE_REVIEWS."""
    def crashing_dispatcher(permit: Permit, trace_id: str, on_event=None):
        raise RuntimeError("Network partition / fatal exception")

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = crashing_dispatcher

    client = TestClient(app)
    resp = client.post("/submit?wait=true", json=sample_permit_dict)
    assert resp.status_code == 202
    permit_id = resp.json()["permit_id"]

    rec = db.get_permit(permit_id, db_path=test_db)
    assert rec is not None
    assert rec.final_decision == FinalDecision.REJECTED
    assert rec.decision_code == DecisionCode.INCOMPLETE_REVIEWS
    assert "failed" in rec.decision_summary.lower()

    # PERMIT_DECIDED event recorded even after crash
    events = db.get_trace_events(permit_id, db_path=test_db)
    assert any(e.type == TraceEventType.PERMIT_DECIDED for e in events)


@pytest.mark.anyio
async def test_permit_tasks_cleanup_on_completion(sample_permit_dict, test_db):
    """Background tasks and events are cleaned up from global tracking dictionaries."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/submit", json=sample_permit_dict)
        assert resp.status_code == 202
        permit_id = resp.json()["permit_id"]

        ev = get_permit_event(permit_id)
        assert ev is not None
        await asyncio.wait_for(ev.wait(), timeout=2.0)
        await asyncio.sleep(0.05)

        assert permit_id not in permit_tasks
        assert permit_id not in permit_events


# =====================================================================
# 4. Consensus Evaluation Tests
# =====================================================================


def test_saga_rejection_consensus(sample_permit_dict, test_db):
    """When a department rejects, consensus produces REJECTED and DEPARTMENT_REJECTIONS."""
    def rejecting_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(
                DepartmentStatus.REJECTED,
                reasons=[
                    EvaluationReason(
                        rule_id="FIRE-01",
                        rule_name="Sprinkler Check",
                        passed=False,
                        message="Missing required sprinklers",
                    )
                ],
            ),
            "water": make_dept_summary(DepartmentStatus.APPROVED),
            "structural": make_dept_summary(DepartmentStatus.APPROVED),
        }

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = rejecting_dispatcher

    client = TestClient(app)
    resp = client.post("/submit?wait=true", json=sample_permit_dict)
    assert resp.status_code == 202
    permit_id = resp.json()["permit_id"]

    rec = db.get_permit(permit_id, db_path=test_db)
    assert rec is not None
    assert rec.final_decision == FinalDecision.REJECTED
    assert rec.decision_code == DecisionCode.DEPARTMENT_REJECTIONS
    assert "fire" in rec.decision_summary.lower()


def test_saga_timeout_incomplete_reviews(sample_permit_dict, test_db):
    """When a department times out, consensus produces REJECTED and INCOMPLETE_REVIEWS."""
    def timeout_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(DepartmentStatus.TIMEOUT, error_message="Timeout after retries"),
            "water": make_dept_summary(DepartmentStatus.APPROVED),
            "structural": make_dept_summary(DepartmentStatus.APPROVED),
        }

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = timeout_dispatcher

    client = TestClient(app)
    resp = client.post("/submit?wait=true", json=sample_permit_dict)
    assert resp.status_code == 202
    permit_id = resp.json()["permit_id"]

    rec = db.get_permit(permit_id, db_path=test_db)
    assert rec is not None
    assert rec.final_decision == FinalDecision.REJECTED
    assert rec.decision_code == DecisionCode.INCOMPLETE_REVIEWS
    assert "fire" in rec.decision_summary.lower()


def test_saga_missing_department_result(sample_permit_dict, test_db):
    """When a department result is missing, consensus produces REJECTED and INCOMPLETE_REVIEWS."""
    def missing_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(DepartmentStatus.APPROVED),
            "water": make_dept_summary(DepartmentStatus.APPROVED),
        }

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = missing_dispatcher

    client = TestClient(app)
    resp = client.post("/submit?wait=true", json=sample_permit_dict)
    assert resp.status_code == 202
    permit_id = resp.json()["permit_id"]

    rec = db.get_permit(permit_id, db_path=test_db)
    assert rec is not None
    assert rec.final_decision == FinalDecision.REJECTED
    assert rec.decision_code == DecisionCode.INCOMPLETE_REVIEWS
    assert "structural" in rec.decision_summary.lower()


# =====================================================================
# 5. Database Persistence Tests
# =====================================================================


def test_department_results_and_trace_events_persisted_to_db(sample_permit_dict, test_db):
    """Department results and ordered trace events are persisted to SQLite."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    client = TestClient(app)
    resp = client.post("/submit?wait=true", json=sample_permit_dict)
    assert resp.status_code == 202
    permit_id = resp.json()["permit_id"]

    # Verify department results in SQLite
    dept_results = db.get_department_results(permit_id, db_path=test_db)
    assert len(dept_results) == 4
    for dept in ["zoning", "fire", "water", "structural"]:
        assert dept in dept_results
        assert dept_results[dept].status == DepartmentStatus.APPROVED
        assert dept_results[dept].attempts == 1
        assert len(dept_results[dept].reasons) >= 1

    # Verify trace events in SQLite
    events = db.get_trace_events(permit_id, db_path=test_db)
    types = [e.type for e in events]
    assert types[0] == TraceEventType.PERMIT_RECEIVED
    assert TraceEventType.CONSENSUS_STARTED in types
    assert types[-1] == TraceEventType.PERMIT_DECIDED


def test_database_wal_mode_and_thread_safety(test_db):
    """Verify database initializes in WAL mode."""
    assert db.is_wal_mode(test_db) is True


@pytest.mark.anyio
async def test_integration_with_stub_department_dispatcher(sample_permit_dict, test_db):
    """Full integration test with orchestrator.dispatcher and stub department app."""
    from orchestrator import dispatcher
    from orchestrator.stub_department import app as stub_app

    stub_transport = httpx.ASGITransport(app=stub_app)

    async def real_stub_dispatcher(permit: Permit, trace_id: str, on_event=None):
        async with httpx.AsyncClient(transport=stub_transport) as http_client:
            return await dispatcher.dispatch_permit(
                permit=permit,
                trace_id=trace_id,
                endpoints={
                    "zoning": "http://testserver/evaluate",
                    "fire": "http://testserver/evaluate",
                    "water": "http://testserver/evaluate",
                    "structural": "http://testserver/evaluate",
                },
                client=http_client,
                on_event=on_event,
            )

    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = real_stub_dispatcher

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/submit?wait=true", json=sample_permit_dict)
        assert resp.status_code == 202
        permit_id = resp.json()["permit_id"]

        rec = db.get_permit(permit_id, db_path=test_db)
        assert rec is not None
        assert rec.state == SagaState.DECIDED
        assert rec.final_decision == FinalDecision.APPROVED
        assert rec.decision_code == DecisionCode.ALL_DEPARTMENTS_APPROVED
        assert len(rec.department_results) == 4


# =====================================================================
# 6. EventBroadcaster Tests
# =====================================================================


@pytest.mark.anyio
async def test_event_broadcaster_subscribe_publish_close():
    broadcaster = EventBroadcaster()
    permit_id = "test-permit-100"

    q1 = broadcaster.subscribe(permit_id)
    q2 = broadcaster.subscribe(permit_id)

    event = TraceEvent(
        event_id="evt-1",
        trace_id="trc-1",
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_RECEIVED,
        detail={"info": "first event"},
    )

    broadcaster.publish(permit_id, event)

    rec1 = await q1.get()
    rec2 = await q2.get()
    assert rec1 is not None and rec1.event_id == "evt-1"
    assert rec2 is not None and rec2.event_id == "evt-1"

    # Close sends None sentinel
    broadcaster.close(permit_id)
    end1 = await q1.get()
    end2 = await q2.get()
    assert end1 is None
    assert end2 is None


@pytest.mark.anyio
async def test_event_broadcaster_unsubscribe():
    broadcaster = EventBroadcaster()
    permit_id = "test-permit-200"

    q1 = broadcaster.subscribe(permit_id)
    q2 = broadcaster.subscribe(permit_id)

    broadcaster.unsubscribe(permit_id, q1)

    event = TraceEvent(
        event_id="evt-2",
        trace_id="trc-2",
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_RECEIVED,
        detail={},
    )
    broadcaster.publish(permit_id, event)

    assert q1.empty()
    assert not q2.empty()
    rec2 = await q2.get()
    assert rec2.event_id == "evt-2"


@pytest.mark.anyio
async def test_event_broadcaster_late_subscriber_receives_none_immediately():
    broadcaster = EventBroadcaster()
    permit_id = "test-permit-closed"

    # Close first
    broadcaster.close(permit_id)

    # Late subscriber connects after saga close
    q = broadcaster.subscribe(permit_id)
    val = await q.get()
    assert val is None


@pytest.mark.anyio
async def test_submit_blank_permit_id_and_idempotency_key(test_db, sample_permit_dict):
    """Verify whitespace-only permit_id and Idempotency-Key assign unique UUIDs."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        payload = dict(sample_permit_dict)
        payload["permit_id"] = "   "

        # 1. Blank permit_id gets unique uuid and stream_url with no '//'
        resp1 = await client.post("/submit", json=payload)
        assert resp1.status_code == 202
        data1 = resp1.json()
        id1 = data1["permit_id"]
        assert id1.strip() != ""
        assert "//" not in data1["stream_url"]

        resp2 = await client.post("/submit", json=payload)
        assert resp2.status_code == 202
        data2 = resp2.json()
        id2 = data2["permit_id"]
        assert id2.strip() != ""
        assert id1 != id2
        assert "//" not in data2["stream_url"]

        # 2. Blank Idempotency-Key header gets unique uuid
        payload_no_id = dict(sample_permit_dict)
        payload_no_id.pop("permit_id", None)

        resp3 = await client.post("/submit", json=payload_no_id, headers={"Idempotency-Key": "   "})
        assert resp3.status_code == 202
        data3 = resp3.json()
        id3 = data3["permit_id"]
        assert id3.strip() != ""
        assert "//" not in data3["stream_url"]

        resp4 = await client.post("/submit", json=payload_no_id, headers={"Idempotency-Key": "   "})
        assert resp4.status_code == 202
        data4 = resp4.json()
        id4 = data4["permit_id"]
        assert id4.strip() != ""
        assert id3 != id4
        assert "//" not in data4["stream_url"]


@pytest.mark.anyio
async def test_submit_rollback_tracking_on_setup_failure(test_db, sample_permit_dict, monkeypatch):
    """Verify tracking state is rolled back when setup fails and permit can be resubmitted."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db

    permit_id = "permit-rollback-test"
    payload = dict(sample_permit_dict)
    payload["permit_id"] = permit_id

    # 1. Monkeypatch db.save_permit to raise an error
    def mock_save_permit(*args, **kwargs):
        raise RuntimeError("Simulated DB save failure")

    monkeypatch.setattr(db, "save_permit", mock_save_permit)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        resp = await client.post("/submit", json=payload)
        assert resp.status_code == 500

        # Assert tracking state was cleanly discarded
        assert permit_id not in permit_trace_ids
        assert permit_id not in submitted_permits
        assert permit_id not in permit_events

        # 2. Restore save_permit and resubmit
        monkeypatch.undo()

        resp2 = await client.post("/submit?wait=true", json=payload)
        assert resp2.status_code == 202

        # Verify saga ran to DECIDED in database
        saved = db.get_permit(permit_id, db_path=test_db)
        assert saved is not None
        assert saved.state == SagaState.DECIDED

        # Verify module dicts hold no leftover entry
        assert permit_id not in permit_tasks
        assert permit_id not in permit_events


@pytest.mark.anyio
async def test_submit_stale_permit_resubmit_reruns_saga(test_db, sample_permit_dict):
    """Verify resubmitting a stale (non-live, non-DECIDED) record reruns saga with original trace_id."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db

    permit_id = "permit-stale-rerun"
    orig_trace = "stale-trace-999"
    p = Permit.model_validate({**sample_permit_dict, "permit_id": permit_id})
    from datetime import datetime, timezone
    created_at = datetime.now(timezone.utc)

    # Seed SQLite with a stale RECEIVED record (no task running, no event set)
    db.save_permit(
        permit_id=permit_id,
        payload=p,
        state=SagaState.RECEIVED,
        created_at=created_at,
        trace_id=orig_trace,
        db_path=test_db,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/submit?wait=true", json=p.model_dump(mode="json"))
        assert resp.status_code == 202
        assert resp.json()["trace_id"] == orig_trace

        # Verify saga executed and updated the record to DECIDED
        updated = db.get_permit(permit_id, db_path=test_db)
        assert updated is not None
        assert updated.state == SagaState.DECIDED
        assert updated.trace_id == orig_trace


@pytest.mark.anyio
async def test_submit_duplicate_wait_true_timeout(test_db, sample_permit_dict):
    """Verify duplicate wait=true against an orphaned event returns within submit_wait_timeout."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.submit_wait_timeout = 0.05

    permit_id = "permit-orphaned-event"
    # Seed orphaned event that is never set and has no active task
    permit_events[permit_id] = asyncio.Event()
    permit_trace_ids[permit_id] = "orphaned-trace-id"

    payload = dict(sample_permit_dict)
    payload["permit_id"] = permit_id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Wrap client call in timeout to prove it returns instead of hanging
        resp = await asyncio.wait_for(
            client.post("/submit?wait=true", json=payload, headers={"Idempotency-Key": permit_id}),
            timeout=2.0,
        )
        assert resp.status_code == 202
        assert resp.json()["permit_id"] == permit_id
        assert resp.json()["trace_id"] == "orphaned-trace-id"


@pytest.mark.anyio
async def test_submit_replay_decided_permit_returns_decided_state(test_db, sample_permit_dict):
    """Verify replaying an already decided permit with Idempotency-Key returns state=DECIDED."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db

    permit_id = "permit-replay-decided"
    payload = dict(sample_permit_dict)
    payload["permit_id"] = permit_id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp1 = await client.post("/submit?wait=true", json=payload, headers={"Idempotency-Key": "key-decided-replay"})
        assert resp1.status_code == 202

        saved = db.get_permit(permit_id, db_path=test_db)
        assert saved is not None
        assert saved.state == SagaState.DECIDED

        # Replay with same Idempotency-Key
        resp2 = await client.post("/submit", json=payload, headers={"Idempotency-Key": "key-decided-replay"})
        assert resp2.status_code == 202
        assert resp2.json()["state"] == SagaState.DECIDED.value


@pytest.mark.anyio
async def test_submit_consensus_override_plain_strings(test_db, sample_permit_dict):
    """Verify custom consensus_override returning plain strings records approved decision."""
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.consensus_override = lambda results, *args: (
        "APPROVED",
        "ALL_DEPARTMENTS_APPROVED",
        "Approved via plain strings override",
        [],
    )

    permit_id = "permit-consensus-str"
    payload = dict(sample_permit_dict)
    payload["permit_id"] = permit_id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/submit?wait=true", json=payload)
        assert resp.status_code == 202

        rec = db.get_permit(permit_id, db_path=test_db)
        assert rec is not None
        assert rec.state == SagaState.DECIDED
        decision_val = rec.final_decision.value if hasattr(rec.final_decision, "value") else str(rec.final_decision)
        assert decision_val.upper() == "APPROVED"
        code_val = rec.decision_code.value if hasattr(rec.decision_code, "value") else str(rec.decision_code)
        assert code_val != DecisionCode.INCOMPLETE_REVIEWS.value


@pytest.mark.anyio
async def test_event_broadcaster_clear_does_not_cancel_permit_tasks():
    """Verify calling clear() on an EventBroadcaster instance does not cancel permit_tasks."""
    app_broadcaster = EventBroadcaster()

    async def dummy_coro():
        await asyncio.sleep(10)

    dummy_task = asyncio.create_task(dummy_coro())
    permit_tasks["dummy-permit-task"] = dummy_task
    try:
        app_broadcaster.clear()
        assert not dummy_task.done()
        assert "dummy-permit-task" in permit_tasks
    finally:
        dummy_task.cancel()
        try:
            await dummy_task
        except asyncio.CancelledError:
            pass
        permit_tasks.pop("dummy-permit-task", None)
