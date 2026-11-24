"""
Unit and integration tests for SSE stream and live browser viewing handler (T5).
Tests orchestrator/handlers/stream.py:
- Replay guarantee: streams past recorded TraceEvents from SQLite first.
- Decided permit: immediately emits event: done.
- Live streaming: subscribes to queue, streams new events, emits done.
- Event deduplication between SQLite replay and live queue.
- Status query: GET /permits/{permit_id} from SQLite with 404 and wait.
- Browser live HTML endpoints: GET / and GET /live.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, List
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contract.models import (
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    FinalDecision,
    Permit,
    PermitStatusResponse,
    SagaState,
    SpanId,
    StreamDonePayload,
    TraceEvent,
    TraceEventType,
)
from orchestrator import db
from orchestrator.handlers.stream import (
    EventBroadcaster,
    broadcaster,
    get_broadcaster,
    router as stream_router,
    stream_permit,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_broadcaster():
    get_broadcaster().clear()
    yield
    get_broadcaster().clear()


@pytest.fixture
def temp_db(tmp_path: Path):
    """Initializes an isolated temporary SQLite database."""
    db_file = tmp_path / "test_permits.db"
    db.init_db(db_file)
    db.set_default_db_path(db_file)
    yield db_file
    # Cleanup
    db.reset_db(db_file)


@pytest.fixture
def app(temp_db: Path) -> FastAPI:
    """FastAPI application configured with stream router."""
    test_app = FastAPI()
    test_app.include_router(stream_router)
    return test_app


@pytest.fixture
def sample_permit_payload() -> dict:
    return {
        "permit_id": "pm-test-stream-001",
        "project_title": "Backyard Studio ADU",
        "estimated_cost_usd": 65000,
        "property_address": {
            "street": "100 Test Lane",
            "city": "San Jose",
            "state": "CA",
            "zip": "95125",
        },
        "applicant": {
            "name": "Jane Tester",
            "role": "homeowner",
            "email": "jane@example.com",
        },
        "zoning": {
            "zone_code": "R-1",
            "building_height_ft": 15.0,
            "lot_area_sqft": 6000,
            "building_footprint_sqft": 800,
            "setback_front_ft": 30.0,
            "setback_rear_ft": 20.0,
            "setback_side_ft": 10.0,
        },
        "fire": {
            "occupancy_type": "residential",
            "occupant_load": 2,
            "has_sprinkler_system": False,
            "egress_exits_count": 2,
            "distance_to_hydrant_ft": 150,
            "has_fire_alarm": True,
        },
        "water": {
            "water_meter_size_inches": 1.0,
            "daily_water_usage_gpd": 150,
            "has_backflow_preventer": True,
            "has_stormwater_plan": True,
        },
        "structural": {
            "stories_count": 1,
            "construction_type": "wood_frame",
            "seismic_design_category": "C",
            "foundation_type": "slab_on_grade",
        },
    }


def test_get_permit_status_from_db(app: FastAPI, temp_db: Path, sample_permit_payload: dict):
    """GET /permits/{permit_id} returns accurate status from SQLite db."""
    client = TestClient(app)
    permit_id = sample_permit_payload["permit_id"]

    # 1. 404 when permit does not exist
    resp_404 = client.get(f"/permits/{permit_id}")
    assert resp_404.status_code == 404
    assert f"Permit '{permit_id}' not found" in resp_404.json()["detail"]

    # 2. Save permit in DB
    now = datetime.now(timezone.utc)
    db.save_permit(
        permit_id=permit_id,
        payload=sample_permit_payload,
        state=SagaState.RECEIVED,
        created_at=now,
        trace_id="tr-test-001",
    )

    resp = client.get(f"/permits/{permit_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["permit_id"] == permit_id
    assert data["state"] == SagaState.RECEIVED.value
    assert data["final_decision"] == FinalDecision.PENDING.value

    # 3. Update permit decision
    decided_at = datetime.now(timezone.utc)
    db.update_permit_decision(
        permit_id=permit_id,
        state=SagaState.DECIDED,
        final_decision=FinalDecision.APPROVED,
        decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
        decision_summary="All 4 departments approved",
        decided_at=decided_at,
    )
    db.save_department_result(
        permit_id=permit_id,
        department=DepartmentName.ZONING,
        status=DepartmentStatus.APPROVED,
        attempts=1,
        latency_ms=45.2,
        reasons=[],
    )

    resp_decided = client.get(f"/permits/{permit_id}")
    assert resp_decided.status_code == 200
    decided_data = resp_decided.json()
    assert decided_data["state"] == SagaState.DECIDED.value
    assert decided_data["final_decision"] == FinalDecision.APPROVED.value
    assert decided_data["decision_code"] == DecisionCode.ALL_DEPARTMENTS_APPROVED.value
    assert "zoning" in decided_data["department_results"]
    assert decided_data["department_results"]["zoning"]["status"] == DepartmentStatus.APPROVED.value


@pytest.mark.anyio
async def test_get_permit_status_wait(app: FastAPI, temp_db: Path, sample_permit_payload: dict):
    """GET /permits/{permit_id}?wait=true awaits saga completion."""
    permit_id = sample_permit_payload["permit_id"]
    now = datetime.now(timezone.utc)
    db.save_permit(
        permit_id=permit_id,
        payload=sample_permit_payload,
        state=SagaState.EVALUATING,
        created_at=now,
        trace_id="tr-test-wait",
    )

    async def complete_permit_after_delay():
        await asyncio.sleep(0.15)
        db.update_permit_decision(
            permit_id=permit_id,
            state=SagaState.DECIDED,
            final_decision=FinalDecision.APPROVED,
            decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
            decision_summary="Approved successfully",
            decided_at=datetime.now(timezone.utc),
        )

    task = asyncio.create_task(complete_permit_after_delay())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/permits/{permit_id}?wait=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == SagaState.DECIDED.value
        assert data["final_decision"] == FinalDecision.APPROVED.value

    await task


def test_stream_not_found(app: FastAPI, temp_db: Path):
    """GET /permits/{permit_id}/stream returns 404 for unknown permit."""
    client = TestClient(app)
    resp = client.get("/permits/pm-nonexistent-123/stream")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_stream_replay_decided_permit(app: FastAPI, temp_db: Path, sample_permit_payload: dict):
    """
    Replay guarantee for already-decided permit:
    - Streams all past recorded TraceEvents from SQLite first with event: trace_event
    - Immediately emits event: done with StreamDonePayload
    """
    permit_id = sample_permit_payload["permit_id"]
    trace_id = "tr-replay-001"
    now = datetime.now(timezone.utc)

    db.save_permit(
        permit_id=permit_id,
        payload=sample_permit_payload,
        state=SagaState.DECIDED,
        created_at=now,
        trace_id=trace_id,
    )
    db.update_permit_decision(
        permit_id=permit_id,
        state=SagaState.DECIDED,
        final_decision=FinalDecision.APPROVED,
        decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
        decision_summary="Consensus: Approved",
        decided_at=now,
    )

    # Append 3 historical trace events
    ev1 = TraceEvent(
        event_id="evt-001",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_RECEIVED,
        ts=now,
        detail={"info": "Received"},
    )
    ev2 = TraceEvent(
        event_id="evt-002",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.CONSENSUS,
        type=TraceEventType.CONSENSUS_STARTED,
        ts=now,
        detail={"departments_received": 4},
    )
    ev3 = TraceEvent(
        event_id="evt-003",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_DECIDED,
        ts=now,
        detail={"final_decision": "APPROVED"},
    )
    db.append_trace_event(ev1)
    db.append_trace_event(ev2)
    db.append_trace_event(ev3)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/permits/{permit_id}/stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        content = resp.text
        assert "event: trace_event" in content
        assert "event: done" in content

        # Split SSE blocks
        blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
        assert len(blocks) == 4, f"Expected 3 trace events + 1 done block, got {len(blocks)}: {blocks}"

        # Verify block 0..2 are trace events in order
        for i, expected_id in enumerate(["evt-001", "evt-002", "evt-003"]):
            assert blocks[i].startswith("event: trace_event")
            data_line = [line for line in blocks[i].split("\n") if line.startswith("data: ")][0]
            data_json = json.loads(data_line[6:])
            assert data_json["event_id"] == expected_id
            assert data_json["permit_id"] == permit_id

        # Verify last block is event: done with StreamDonePayload
        done_block = blocks[3]
        assert done_block.startswith("event: done")
        done_line = [line for line in done_block.split("\n") if line.startswith("data: ")][0]
        done_json = json.loads(done_line[6:])
        assert done_json["permit_id"] == permit_id
        assert done_json["final_decision"] == FinalDecision.APPROVED.value


@pytest.mark.anyio
async def test_stream_live_events_and_done(app: FastAPI, temp_db: Path, sample_permit_payload: dict):
    """
    Live streaming test:
    - Subscribes to live event queue.
    - Yields past recorded events first.
    - Streams new live events as they arrive.
    - Emits event: done upon completion.
    """
    permit_id = sample_permit_payload["permit_id"]
    trace_id = "tr-live-002"
    now = datetime.now(timezone.utc)

    # Permit in EVALUATING state
    db.save_permit(
        permit_id=permit_id,
        payload=sample_permit_payload,
        state=SagaState.EVALUATING,
        created_at=now,
        trace_id=trace_id,
    )

    # 1 initial past event
    root_ev = TraceEvent(
        event_id="evt-past-1",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_RECEIVED,
        ts=now,
        detail={"state": "RECEIVED"},
    )
    db.append_trace_event(root_ev)

    active_broadcaster = get_broadcaster(app)

    async def simulate_live_saga_publisher():
        await asyncio.sleep(0.08)
        # Push 1st live event
        live_ev1 = TraceEvent(
            event_id="evt-live-2",
            trace_id=trace_id,
            permit_id=permit_id,
            span=SpanId.DEPT_ZONING,
            type=TraceEventType.CALL_SUCCEEDED,
            ts=datetime.now(timezone.utc),
            detail={"status": "APPROVED", "attempt": 1},
        )
        active_broadcaster.publish(permit_id, live_ev1)

        await asyncio.sleep(0.05)
        # Push 2nd live event
        live_ev2 = TraceEvent(
            event_id="evt-live-3",
            trace_id=trace_id,
            permit_id=permit_id,
            span=SpanId.CONSENSUS,
            type=TraceEventType.CONSENSUS_STARTED,
            ts=datetime.now(timezone.utc),
            detail={"departments_received": 4},
        )
        active_broadcaster.publish(permit_id, live_ev2)

        await asyncio.sleep(0.05)
        # Update permit decision in DB and close stream with StreamDonePayload
        db.update_permit_decision(
            permit_id=permit_id,
            state=SagaState.DECIDED,
            final_decision=FinalDecision.APPROVED,
            decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
            decision_summary="All approved",
            decided_at=datetime.now(timezone.utc),
        )
        active_broadcaster.publish(
            permit_id,
            StreamDonePayload(permit_id=permit_id, final_decision=FinalDecision.APPROVED),
        )

    pub_task = asyncio.create_task(simulate_live_saga_publisher())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream("GET", f"/permits/{permit_id}/stream") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            received_events: List[str] = []
            done_received = False

            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    block = block.strip()
                    if not block:
                        continue
                    if block.startswith("event: trace_event"):
                        data_line = [l for l in block.split("\n") if l.startswith("data: ")][0]
                        ev_json = json.loads(data_line[6:])
                        received_events.append(ev_json["event_id"])
                    elif block.startswith("event: done"):
                        data_line = [l for l in block.split("\n") if l.startswith("data: ")][0]
                        done_json = json.loads(data_line[6:])
                        assert done_json["permit_id"] == permit_id
                        assert done_json["final_decision"] == FinalDecision.APPROVED.value
                        done_received = True

            assert received_events == ["evt-past-1", "evt-live-2", "evt-live-3"]
            assert done_received is True

    await pub_task


@pytest.mark.anyio
async def test_stream_deduplication(app: FastAPI, temp_db: Path, sample_permit_payload: dict):
    """Verifies events are not duplicated between DB replay and live queue."""
    permit_id = sample_permit_payload["permit_id"]
    trace_id = "tr-dedup-003"
    now = datetime.now(timezone.utc)

    db.save_permit(
        permit_id=permit_id,
        payload=sample_permit_payload,
        state=SagaState.EVALUATING,
        created_at=now,
        trace_id=trace_id,
    )

    dup_event = TraceEvent(
        event_id="evt-dup-1",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_RECEIVED,
        ts=now,
        detail={"msg": "Saved in DB and published on queue"},
    )
    # 1. Saved in DB
    db.append_trace_event(dup_event)

    active_broadcaster = get_broadcaster(app)

    async def push_duplicate_and_close():
        await asyncio.sleep(0.04)
        # Push duplicate event to queue
        active_broadcaster.publish(permit_id, dup_event)
        await asyncio.sleep(0.04)
        # Then close with None sentinel
        db.update_permit_decision(
            permit_id=permit_id,
            state=SagaState.DECIDED,
            final_decision=FinalDecision.REJECTED,
            decision_code=DecisionCode.DEPARTMENT_REJECTIONS,
            decision_summary="Rejections found",
            decided_at=datetime.now(timezone.utc),
        )
        active_broadcaster.close(permit_id)

    pub_task = asyncio.create_task(push_duplicate_and_close())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/permits/{permit_id}/stream")
        assert resp.status_code == 200

        content = resp.text
        blocks = [b.strip() for b in content.strip().split("\n\n") if b.strip()]
        trace_blocks = [b for b in blocks if b.startswith("event: trace_event")]
        done_blocks = [b for b in blocks if b.startswith("event: done")]

        assert len(trace_blocks) == 1, "Duplicate event was re-emitted!"
        assert len(done_blocks) == 1
        assert "REJECTED" in done_blocks[0]

    await pub_task


def test_browser_live_html_page(app: FastAPI):
    """GET / and GET /live serve the interactive HTML dashboard."""
    client = TestClient(app)

    for path in ["/", "/live"]:
        resp = client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text

        # Verify key elements
        assert "Permit Orchestrator Live Stream" in body
        assert "EventSource" in body
        assert "/permits/" in body
        assert "/submit" in body
        assert "trace_event" in body
        assert "done" in body
        assert "Zoning" in body
        assert "Fire" in body
        assert "Water" in body
        assert "Structural" in body
        assert "pm-fixture-approved-001" in body
        assert "pm-fixture-rejected-fire-002" in body
        assert "pm-fixture-timeout-offline-003" in body


@pytest.mark.anyio
async def test_stream_backward_compat_with_orchestrator_state(temp_db: Path, sample_permit_payload: dict):
    """Verifies stream handler integrates seamlessly with app.state.orchestrator."""
    from orchestrator.app import OrchestratorApp

    test_app = FastAPI()
    orchestrator_state = OrchestratorApp()
    test_app.state.orchestrator = orchestrator_state
    test_app.include_router(stream_router)

    permit_id = sample_permit_payload["permit_id"]
    trace_id = "tr-compat-004"
    now = datetime.now(timezone.utc)

    # Put permit in memory state
    orchestrator_state.permits_db[permit_id] = PermitStatusResponse(
        permit_id=permit_id,
        trace_id=trace_id,
        state=SagaState.EVALUATING,
        created_at=now,
    )

    ev_root = TraceEvent(
        event_id="evt-mem-1",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_RECEIVED,
        ts=now,
    )
    orchestrator_state.record_event(permit_id, ev_root)

    active_broadcaster = get_broadcaster(test_app)

    async def push_memory_event_and_close():
        await asyncio.sleep(0.05)
        ev_live = TraceEvent(
            event_id="evt-mem-2",
            trace_id=trace_id,
            permit_id=permit_id,
            span=SpanId.CONSENSUS,
            type=TraceEventType.CONSENSUS_STARTED,
            ts=datetime.now(timezone.utc),
        )
        orchestrator_state.record_event(permit_id, ev_live)
        active_broadcaster.publish(permit_id, ev_live)
        await asyncio.sleep(0.05)
        orchestrator_state.permits_db[permit_id].state = SagaState.DECIDED
        orchestrator_state.permits_db[permit_id].final_decision = FinalDecision.APPROVED
        orchestrator_state.close_stream_subscribers(permit_id)
        active_broadcaster.close(permit_id)

    pub_task = asyncio.create_task(push_memory_event_and_close())

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/permits/{permit_id}/stream")
        assert resp.status_code == 200
        content = resp.text
        assert "evt-mem-1" in content
        assert "evt-mem-2" in content
        assert "event: done" in content
        assert "APPROVED" in content

    await pub_task


@pytest.mark.anyio
async def test_stream_subscriber_cleanup(app: FastAPI, temp_db: Path, sample_permit_payload: dict):
    """Verifies that active subscriber queue is removed on stream close."""
    permit_id = sample_permit_payload["permit_id"]
    db.save_permit(
        permit_id=permit_id,
        payload=sample_permit_payload,
        state=SagaState.DECIDED,
        created_at=datetime.now(timezone.utc),
    )
    db.update_permit_decision(
        permit_id=permit_id,
        state=SagaState.DECIDED,
        final_decision=FinalDecision.APPROVED,
        decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
        decision_summary="Approved",
        decided_at=datetime.now(timezone.utc),
    )

    active_broadcaster = get_broadcaster(app)
    initial_subscribers = len(active_broadcaster._subscribers.get(permit_id, []))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get(f"/permits/{permit_id}/stream")
        assert resp.status_code == 200

    # Ensure clean unsubscribe
    final_subscribers = len(active_broadcaster._subscribers.get(permit_id, []))
    assert final_subscribers == initial_subscribers == 0


@pytest.mark.anyio
async def test_stream_does_not_drop_events_when_saga_decides_mid_replay(app: FastAPI, temp_db: Path, sample_permit_payload: dict):
    """
    Verifies that if the saga reaches consensus and decides while past events are being replayed,
    events queued during replay (such as CONSENSUS_STARTED and PERMIT_DECIDED) are not dropped.
    """
    permit_id = "test-mid-replay-race-002"
    trace_id = "tr-race-mid-replay"
    now = datetime.now(timezone.utc)

    db.save_permit(
        permit_id=permit_id,
        payload=sample_permit_payload,
        state=SagaState.EVALUATING,
        created_at=now,
        trace_id=trace_id,
    )
    ev_root = TraceEvent(
        event_id="evt-race-1",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_RECEIVED,
        ts=now,
        detail={"state": "RECEIVED"},
    )
    db.append_trace_event(ev_root)

    active_broadcaster = get_broadcaster(app)

    response = await stream_permit(permit_id, request=None)
    body = response.body_iterator

    # Consume the first replayed event, suspending generator mid-replay
    first_chunk = await body.__anext__()
    assert "evt-race-1" in first_chunk

    # Simulate saga deciding while generator was suspended in replay
    db.update_permit_decision(
        permit_id=permit_id,
        state=SagaState.DECIDED,
        final_decision=FinalDecision.APPROVED,
        decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
        decision_summary="Approved",
        decided_at=datetime.now(timezone.utc),
    )
    decided_event = TraceEvent(
        event_id="evt-race-decided",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        type=TraceEventType.PERMIT_DECIDED,
        ts=datetime.now(timezone.utc),
        detail={"final_decision": "APPROVED"},
    )
    active_broadcaster.publish(permit_id, decided_event)
    active_broadcaster.close(permit_id)

    rest = "".join([chunk async for chunk in body])
    assert "evt-race-decided" in rest
    assert rest.index("evt-race-decided") < rest.index("event: done")
    assert rest.count("event: done") == 1

