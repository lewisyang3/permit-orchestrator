"""
Tests for Permit Submission Handler (orchestrator/handlers/submit.py).

Verifies:
- EventBroadcaster subscription, broadcasting, unsubscribing, and close sentinel.
- APIRouter POST /submit validation, response model (202 Accepted), and stream_url.
- Initial permit persistence to SQLite with SagaState.RECEIVED.
- Root PERMIT_RECEIVED trace event emitted to SQLite and broadcaster.
- Asynchronous saga execution with dispatcher fan-out and consensus evaluation.
- Live event streaming to subscribers during saga execution.
- Persistence of department results and final decision to SQLite.
- Fail-safe consensus recovery on unhandled exceptions.
- Real SQLite database integration using orchestrator.db helpers.
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
    router,
    run_permit_saga,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def sample_permit_dict() -> dict:
    fixture_path = Path("contract/fixtures/permit_approved.json")
    return json.loads(fixture_path.read_text())


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_submit.db"
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
    # Emit simulated events if callback provided
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
# 1. EventBroadcaster Tests
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


# =====================================================================
# 2. POST /submit Endpoint Tests
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


# =====================================================================
# 3. Saga Execution & Consensus Scenarios
# =====================================================================


def test_saga_rejection_consensus(sample_permit_dict, test_db):
    def rejecting_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return {
            "zoning": make_dept_summary(DepartmentStatus.APPROVED),
            "fire": make_dept_summary(DepartmentStatus.REJECTED, reasons=[
                EvaluationReason(
                    rule_id="FIRE-01",
                    rule_name="Sprinkler Check",
                    passed=False,
                    message="Missing required sprinklers",
                )
            ]),
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


def test_saga_failsafe_on_unhandled_dispatcher_exception(sample_permit_dict, test_db):
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

    # Verify PERMIT_DECIDED event recorded even after crash
    events = db.get_trace_events(permit_id, db_path=test_db)
    assert any(e.type == TraceEventType.PERMIT_DECIDED for e in events)


# =====================================================================
# 4. Live Streaming & Broadcaster Subscription during Saga
# =====================================================================


@pytest.mark.anyio
async def test_live_broadcaster_receives_all_saga_events(sample_permit_dict, test_db):
    permit = Permit.model_validate(sample_permit_dict)
    permit_id = permit.permit_id
    trace_id = "test-live-trace-123"

    broadcaster = get_broadcaster()
    queue = broadcaster.subscribe(permit_id)

    db.save_permit(permit_id, payload=permit, state=SagaState.RECEIVED, trace_id=trace_id, db_path=test_db)

    # Run saga in background
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

    # Verify received events through live broadcaster
    event_types = [e.type for e in received_events]
    assert TraceEventType.SPAN_STARTED in event_types
    assert TraceEventType.CALL_SUCCEEDED in event_types
    assert TraceEventType.CONSENSUS_STARTED in event_types
    assert TraceEventType.PERMIT_DECIDED in event_types

    # Verify last event is PERMIT_DECIDED
    assert received_events[-1].type == TraceEventType.PERMIT_DECIDED
    assert received_events[-1].detail["final_decision"] == FinalDecision.APPROVED.value


@pytest.mark.anyio
async def test_async_wait_false_background_completion(sample_permit_dict, test_db):
    app = FastAPI()
    app.include_router(router)
    app.state.db_path = test_db
    app.state.dispatcher_override = mock_approved_dispatcher

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Submit with wait=False (default)
        resp = await client.post("/submit", json=sample_permit_dict)
        assert resp.status_code == 202
        data = resp.json()
        permit_id = data["permit_id"]

        # Wait on background saga completion event
        event = get_permit_event(permit_id)
        assert event is not None
        await asyncio.wait_for(event.wait(), timeout=2.0)

        # Verify DB is updated to DECIDED
        rec = db.get_permit(permit_id, db_path=test_db)
        assert rec is not None
        assert rec.state == SagaState.DECIDED
        assert rec.final_decision == FinalDecision.APPROVED


def test_department_results_and_trace_events_persisted_to_db(sample_permit_dict, test_db):
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


@pytest.mark.anyio
async def test_integration_with_stub_department_dispatcher(sample_permit_dict, test_db):
    """Test full integration with orchestrator.dispatcher and stub department app."""
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
