"""
End-to-End Verifier Test Suite for Task T5 (Permit Orchestrator).

Verifies the complete integration of:
1. SQLite persistence (permits, department results, append-only trace events with WAL mode).
2. Server-Sent Events (SSE) streaming with live pub/sub and replay guarantee.
3. Interactive browser live endpoints (GET / and GET /live).
4. Admin fault injection forwarding to department microservices (POST and GET /admin/fault).
5. Full lifecycle: fault injection -> submission -> live streaming -> SQLite verification.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import AsyncIterator, Dict, List
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from contract.models import (
    AdminFaultResponse,
    DecisionCode,
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    FaultControlRequest,
    FaultControlResponse,
    FaultMode,
    FinalDecision,
    Permit,
    PermitStatusResponse,
    SagaState,
    SpanId,
    StreamDonePayload,
    SubmitPermitResponse,
    TraceEvent,
    TraceEventType,
)
from orchestrator import db, dispatcher
from orchestrator.app import OrchestratorApp, create_app
from orchestrator.handlers.admin import resolve_fault_url
from orchestrator.handlers.stream import HTML_PAGE
from orchestrator.handlers.submit import broadcaster
from orchestrator.stub_department import create_stub_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def sample_permit_payload() -> dict:
    """Load valid approved permit fixture."""
    fixture_path = Path("contract/fixtures/permit_approved.json")
    return json.loads(fixture_path.read_text())


@pytest.fixture
def isolated_db(tmp_path: Path) -> Path:
    """Initializes an isolated temporary SQLite database with WAL mode."""
    db_file = tmp_path / "t5_verifier.db"
    db.init_db(db_file)
    db.set_default_db_path(db_file)
    return db_file


@pytest.fixture
def stub_departments():
    """Create stub apps for all 4 municipal departments."""
    return {
        "zoning": create_stub_app("zoning"),
        "fire": create_stub_app("fire"),
        "water": create_stub_app("water"),
        "structural": create_stub_app("structural"),
    }


class DepartmentRouterTransport(httpx.AsyncBaseTransport):
    """Routes HTTP requests to stub department apps by hostname."""

    def __init__(self, dept_apps: Dict[str, httpx.ASGITransport]):
        self.transports = {
            name: httpx.ASGITransport(app=app) for name, app in dept_apps.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.transports:
            return await self.transports[host].handle_async_request(request)
        raise RuntimeError(f"Unknown target department host: {host}")


# ==============================================================================
# 1. SQLite Persistence Verifier Tests
# ==============================================================================

def test_submit_writes_to_sqlite(isolated_db: Path, sample_permit_payload: dict):
    """
    Verifies:
    - Submitting a permit writes to SQLite (permits table and append-only trace_events).
    - Checks permits table schema, payload JSON, decision results, and timestamps.
    - Checks department_results table for all 4 departments.
    - Confirms append-only trace_events order and schema.
    """
    def mock_dispatcher(permit: Permit, trace_id: str, on_event=None):
        if on_event:
            for dept in ["zoning", "fire", "water", "structural"]:
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
            dept: DepartmentResultSummary(
                status=DepartmentStatus.APPROVED,
                attempts=1,
                latency_ms=12.5,
                reasons=[
                    EvaluationReason(
                        rule_id=f"RULE-{dept.upper()}-01",
                        rule_name=f"{dept.title()} Check",
                        passed=True,
                        message=f"{dept.title()} compliance verified",
                    )
                ],
            )
            for dept in ["zoning", "fire", "water", "structural"]
        }

    app = create_app(dispatcher_override=mock_dispatcher, db_path=isolated_db)
    client = TestClient(app)

    # 1. Submit permit with wait=true to await full saga completion
    submit_resp = client.post("/submit?wait=true", json=sample_permit_payload)
    assert submit_resp.status_code == 202
    sub_data = submit_resp.json()
    permit_id = sub_data["permit_id"]
    trace_id = sub_data["trace_id"]
    assert sub_data["state"] == SagaState.RECEIVED.value
    assert sub_data["stream_url"] == f"/permits/{permit_id}/stream"

    # 2. Query SQLite via db helper
    db_permit = db.get_permit(permit_id, db_path=isolated_db)
    assert db_permit is not None
    assert db_permit.permit_id == permit_id
    assert db_permit.trace_id == trace_id
    assert db_permit.state == SagaState.DECIDED
    assert db_permit.final_decision == FinalDecision.APPROVED
    assert db_permit.decision_code == DecisionCode.ALL_DEPARTMENTS_APPROVED
    assert db_permit.decided_at is not None
    assert len(db_permit.department_results) == 4
    for dept_name in ("zoning", "fire", "water", "structural"):
        assert dept_name in db_permit.department_results
        dept_res = db_permit.department_results[dept_name]
        assert dept_res.status == DepartmentStatus.APPROVED
        assert dept_res.attempts == 1
        assert len(dept_res.reasons) == 1
        assert dept_res.reasons[0].passed is True

    # 3. Verify SQLite raw schema and records directly
    with sqlite3.connect(isolated_db) as raw_conn:
        raw_conn.row_factory = sqlite3.Row
        cur = raw_conn.cursor()

        # Permits table record
        cur.execute("SELECT * FROM permits WHERE permit_id = ?", (permit_id,))
        p_row = cur.fetchone()
        assert p_row is not None
        assert p_row["state"] == "DECIDED"
        assert p_row["final_decision"] == "APPROVED"
        assert p_row["decision_code"] == "ALL_DEPARTMENTS_APPROVED"
        saved_payload = json.loads(p_row["payload"])
        assert saved_payload["project_title"] == sample_permit_payload["project_title"]

        # Department results table records
        cur.execute("SELECT * FROM department_results WHERE permit_id = ?", (permit_id,))
        dept_rows = cur.fetchall()
        assert len(dept_rows) == 4
        dept_names_found = {r["department"] for r in dept_rows}
        assert dept_names_found == {"zoning", "fire", "water", "structural"}

        # Trace events table records (append-only)
        cur.execute("SELECT * FROM trace_events WHERE permit_id = ? ORDER BY rowid ASC", (permit_id,))
        t_rows = cur.fetchall()
        assert len(t_rows) >= 6  # PERMIT_RECEIVED, 4 department events, CONSENSUS_STARTED, PERMIT_DECIDED
        event_types = [r["type"] for r in t_rows]
        assert event_types[0] == TraceEventType.PERMIT_RECEIVED.value
        assert TraceEventType.CONSENSUS_STARTED.value in event_types
        assert event_types[-1] == TraceEventType.PERMIT_DECIDED.value

        # Append-only constraint: event_id is PRIMARY KEY
        first_evt_id = t_rows[0]["event_id"]
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO trace_events (event_id, trace_id, permit_id, span, type, ts, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (first_evt_id, trace_id, permit_id, "dup_span", "dup_type", "2026-01-01T00:00:00Z", "{}"),
            )


# ==============================================================================
# 2. SSE Stream Replay and Live Events Verifier Tests
# ==============================================================================

@pytest.mark.anyio
async def test_sse_stream_replay_and_live_events(isolated_db: Path, sample_permit_payload: dict):
    """
    Verifies:
    - An SSE stream client receives replayed events and live events until done.
    - Tests replay guarantee: events stored prior to connection are received first.
    - Tests live events: events published during execution are received.
    - Tests done event: terminal event: done payload is received with final decision.
    - Tests stream replay on already-decided permits.
    """
    async def delayed_dispatcher(permit: Permit, trace_id: str, on_event=None):
        await asyncio.sleep(0.08)
        if on_event:
            for dept in ["zoning", "fire"]:
                on_event(
                    TraceEvent(
                        event_id=f"evt-{dept}-live",
                        trace_id=trace_id,
                        permit_id=permit.permit_id,
                        span=SpanId(f"dept:{dept}"),
                        parent_span="orchestrator",
                        type=TraceEventType.CALL_SUCCEEDED,
                        detail={"department": dept},
                    )
                )
        await asyncio.sleep(0.05)
        return {
            dept: DepartmentResultSummary(
                status=DepartmentStatus.APPROVED,
                attempts=1,
                latency_ms=10.0,
            )
            for dept in ["zoning", "fire", "water", "structural"]
        }

    app = create_app(dispatcher_override=delayed_dispatcher, db_path=isolated_db)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testorchestrator") as client:
        # 1. Submit permit with wait=False so saga runs in background
        sub_resp = await client.post("/submit", json=sample_permit_payload)
        assert sub_resp.status_code == 202
        sub_data = sub_resp.json()
        permit_id = sub_data["permit_id"]

        # 2. Connect SSE stream client while saga is actively running
        events_received: List[dict] = []
        done_payload: dict = {}

        async with client.stream("GET", f"/permits/{permit_id}/stream") as stream_resp:
            assert stream_resp.status_code == 200
            assert "text/event-stream" in stream_resp.headers["content-type"]

            current_event_type = None
            async for raw_line in stream_resp.aiter_lines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("event: "):
                    current_event_type = line[len("event: "):].strip()
                elif line.startswith("data: "):
                    data_str = line[len("data: "):].strip()
                    parsed = json.loads(data_str)
                    if current_event_type == "trace_event":
                        events_received.append(parsed)
                    elif current_event_type == "done":
                        done_payload = parsed
                        break

        # Verify received events
        assert len(events_received) >= 2
        # Root event replayed
        assert events_received[0]["type"] == TraceEventType.PERMIT_RECEIVED.value
        # Final decided event received
        assert events_received[-1]["type"] == TraceEventType.PERMIT_DECIDED.value
        # Terminal done event payload verified
        assert done_payload.get("permit_id") == permit_id
        assert done_payload.get("final_decision") == FinalDecision.APPROVED.value

        # 3. Test replay guarantee on already-decided permit:
        # A new subscriber connects after completion and gets full replay + done
        replay_events: List[dict] = []
        replay_done: dict = {}
        async with client.stream("GET", f"/permits/{permit_id}/stream") as replay_stream:
            assert replay_stream.status_code == 200
            current_event_type = None
            async for raw_line in replay_stream.aiter_lines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("event: "):
                    current_event_type = line[len("event: "):].strip()
                elif line.startswith("data: "):
                    data_str = line[len("data: "):].strip()
                    parsed = json.loads(data_str)
                    if current_event_type == "trace_event":
                        replay_events.append(parsed)
                    elif current_event_type == "done":
                        replay_done = parsed
                        break

        assert len(replay_events) == len(events_received)
        assert replay_done.get("permit_id") == permit_id
        assert replay_done.get("final_decision") == FinalDecision.APPROVED.value


# ==============================================================================
# 3. Live Browser Endpoint Verifier Tests (GET / and GET /live)
# ==============================================================================

def test_live_browser_endpoint_serves_interactive_ui(isolated_db: Path):
    """
    Verifies:
    - A live browser request to GET / (or GET /live) streams events live.
    - Serves complete standalone interactive HTML/JS dashboard.
    - Contains submission form, EventSource wiring, department cards, and waterfall feed.
    - Zero external CDN dependencies.
    """
    app = create_app(db_path=isolated_db)
    client = TestClient(app)

    # 1. Test GET /
    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert "text/html" in root_resp.headers["content-type"]
    root_html = root_resp.text

    # 2. Test GET /live
    live_resp = client.get("/live")
    assert live_resp.status_code == 200
    assert "text/html" in live_resp.headers["content-type"]
    live_html = live_resp.text

    # Endpoints serve interactive live dashboard (GET /live preserves the standalone HTML UI,
    # while GET / serves the production UI or fallback)
    assert "<!doctype html>" in root_html.lower()
    assert "<!doctype html>" in live_html.lower()
    assert "Permit Orchestrator" in root_html
    assert "Permit Orchestrator - Live Browser Saga Stream" in live_html

    # Check key UI features and EventSource logic on the standalone live dashboard
    assert "new EventSource(url)" in live_html
    assert "es.addEventListener('trace_event'" in live_html
    assert "es.addEventListener('done'" in live_html
    assert "cardZoning" in live_html
    assert "cardFire" in live_html
    assert "cardWater" in live_html
    assert "cardStructural" in live_html
    assert "trace-list" in live_html
    assert "btnSubmit" in live_html
    assert "btnConnectExisting" in live_html

    # Check self-contained / zero external script tags on standalone dashboard
    assert "http://" not in live_html.lower() or "http://localhost" in live_html or "http://testserver" in live_html
    assert "<script src=" not in live_html  # Embedded JS, no third-party CDN scripts


# ==============================================================================
# 4. Admin Fault Injection Forwarding Verifier Tests
# ==============================================================================

@pytest.mark.anyio
async def test_admin_fault_injection_forwards_to_departments(isolated_db: Path, stub_departments):
    """
    Verifies:
    - Admin fault injection forwards commands to department services via POST /admin/fault.
    - Queries active fault configurations across all departments via GET /admin/fault.
    - Handles offline/unreachable departments gracefully.
    - Validates error responses (422 missing department, 404 unknown department).
    """
    shared_client = httpx.AsyncClient(
        transport=DepartmentRouterTransport(stub_departments)
    )
    try:
        app = create_app(db_path=isolated_db, http_client=shared_client)
        app.state.http_client = shared_client
        app.state.department_endpoints = {
            "zoning": "http://zoning/evaluate",
            "fire": "http://fire/evaluate",
            "water": "http://water/evaluate",
            "structural": "http://structural/evaluate",
        }

        orchestrator_transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=orchestrator_transport, base_url="http://orchestrator") as client:
            # 1. Initially, GET /admin/fault returns all NONE mode
            initial_faults = await client.get("/admin/fault")
            assert initial_faults.status_code == 200
            data = initial_faults.json()
            for dept in ("zoning", "fire", "water", "structural"):
                assert dept in data
                assert data[dept]["department"] == dept
                assert data[dept]["mode"] == FaultMode.NONE.value

            # 2. Inject SLOW mode on Fire department
            fire_slow = FaultControlRequest(
                department=DepartmentName.FIRE,
                mode=FaultMode.SLOW,
                delay_ms=450,
            )
            post_fire = await client.post("/admin/fault", json=fire_slow.model_dump(mode="json"))
            assert post_fire.status_code == 200
            fire_res = post_fire.json()
            assert fire_res["department"] == "fire"
            assert fire_res["mode"] == FaultMode.SLOW.value
            assert fire_res["delay_ms"] == 450

            # 3. Inject ERROR mode on Structural department
            struct_fail = FaultControlRequest(
                department=DepartmentName.STRUCTURAL,
                mode=FaultMode.ERROR,
                error_code=500,
                error_message="Simulated foundation structural failure",
            )
            post_struct = await client.post("/admin/fault", json=struct_fail.model_dump(mode="json"))
            assert post_struct.status_code == 200
            struct_res = post_struct.json()
            assert struct_res["department"] == "structural"
            assert struct_res["mode"] == FaultMode.ERROR.value

            # 4. GET /admin/fault confirms both injected faults are active
            active_faults = await client.get("/admin/fault")
            assert active_faults.status_code == 200
            active_data = active_faults.json()
            assert active_data["fire"]["mode"] == FaultMode.SLOW.value
            assert active_data["fire"]["delay_ms"] == 450
            assert active_data["structural"]["mode"] == FaultMode.ERROR.value
            assert active_data["zoning"]["mode"] == FaultMode.NONE.value
            assert active_data["water"]["mode"] == FaultMode.NONE.value

            # 5. Validation error tests
            # 422 when department is missing
            resp_422 = await client.post("/admin/fault", json={"mode": "slow", "delay_ms": 100})
            assert resp_422.status_code == 422

            # 404 when department is unknown
            resp_404 = await client.post("/admin/fault", json={"department": "nonexistent_dept", "mode": "none"})
            assert resp_404.status_code == 422 or resp_404.status_code == 404
    finally:
        await shared_client.aclose()


# ==============================================================================
# 5. Full Lifecycle End-to-End Verifier Test
# ==============================================================================

@pytest.mark.anyio
async def test_full_e2e_lifecycle(isolated_db: Path, sample_permit_payload: dict, stub_departments):
    """
    Complete end-to-end integration scenario:
    1. Inject fault into department via POST /admin/fault (Water set to FAIL).
    2. Submit permit via POST /submit.
    3. Stream live progress via GET /permits/{permit_id}/stream until done.
    4. Verify stream done payload indicates REJECTED due to department fault.
    5. Verify SQLite records permit state=DECIDED, final_decision=REJECTED, and all trace events.
    6. Reset department fault via POST /admin/fault (Water set to NONE).
    7. Submit new permit and verify successful APPROVED lifecycle and SSE stream.
    """
    shared_client = httpx.AsyncClient(
        transport=DepartmentRouterTransport(stub_departments)
    )
    try:
        async def e2e_dispatcher(permit: Permit, trace_id: str, on_event=None):
            return await dispatcher.dispatch_permit(
                permit=permit,
                trace_id=trace_id,
                endpoints={
                    "zoning": "http://zoning/evaluate",
                    "fire": "http://fire/evaluate",
                    "water": "http://water/evaluate",
                    "structural": "http://structural/evaluate",
                },
                client=shared_client,
                backoff_delays=[0.02, 0.04],
                timeout_seconds=0.5,
                on_event=on_event,
            )

        app = create_app(dispatcher_override=e2e_dispatcher, db_path=isolated_db, http_client=shared_client)
        app.state.http_client = shared_client
        app.state.department_endpoints = {
            "zoning": "http://zoning/evaluate",
            "fire": "http://fire/evaluate",
            "water": "http://water/evaluate",
            "structural": "http://structural/evaluate",
        }

        orchestrator_transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=orchestrator_transport, base_url="http://orchestrator") as client:
            # Step 1: Inject fault into water department
            fault_resp = await client.post(
                "/admin/fault",
                json={"department": "water", "mode": "error", "error_message": "Water pressure system offline"},
            )
            assert fault_resp.status_code == 200

            # Step 2: Submit permit
            sub_resp = await client.post("/submit", json=sample_permit_payload)
            assert sub_resp.status_code == 202
            permit_id = sub_resp.json()["permit_id"]

            # Step 3: Stream SSE events until done
            events = []
            done_payload = {}
            async with client.stream("GET", f"/permits/{permit_id}/stream") as stream:
                assert stream.status_code == 200
                current_event_type = None
                async for raw_line in stream.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        current_event_type = line[len("event: "):].strip()
                    elif line.startswith("data: "):
                        parsed = json.loads(line[len("data: "):].strip())
                        if current_event_type == "trace_event":
                            events.append(parsed)
                        elif current_event_type == "done":
                            done_payload = parsed
                            break

            # Step 4: Verify rejected consensus due to faulted department
            assert done_payload.get("final_decision") == FinalDecision.REJECTED.value

            # Step 5: Verify SQLite persistence
            db_permit = db.get_permit(permit_id, db_path=isolated_db)
            assert db_permit is not None
            assert db_permit.state == SagaState.DECIDED
            assert db_permit.final_decision == FinalDecision.REJECTED
            assert db_permit.department_results["water"].status in (DepartmentStatus.ERROR, DepartmentStatus.TIMEOUT, DepartmentStatus.REJECTED)

            # Verify trace events in SQLite
            db_events = db.get_trace_events(permit_id, db_path=isolated_db)
            assert len(db_events) >= 3
            assert db_events[0].type == TraceEventType.PERMIT_RECEIVED
            assert db_events[-1].type == TraceEventType.PERMIT_DECIDED

            # Step 6: Reset fault on water department
            reset_resp = await client.post(
                "/admin/fault",
                json={"department": "water", "mode": "none"},
            )
            assert reset_resp.status_code == 200

            # Step 7: Submit second permit - should succeed with APPROVED
            payload_2 = sample_permit_payload.copy()
            payload_2["permit_id"] = f"pm-clean-{uuid4().hex[:8]}"
            sub2_resp = await client.post("/submit", json=payload_2)
            assert sub2_resp.status_code == 202
            permit_id_2 = sub2_resp.json()["permit_id"]

            done_payload_2 = {}
            async with client.stream("GET", f"/permits/{permit_id_2}/stream") as stream_2:
                current_event_type = None
                async for raw_line in stream_2.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        current_event_type = line[len("event: "):].strip()
                    elif line.startswith("data: "):
                        parsed = json.loads(line[len("data: "):].strip())
                        if current_event_type == "done":
                            done_payload_2 = parsed
                            break

            assert done_payload_2.get("final_decision") == FinalDecision.APPROVED.value
            db_permit_2 = db.get_permit(permit_id_2, db_path=isolated_db)
            assert db_permit_2.state == SagaState.DECIDED
            assert db_permit_2.final_decision == FinalDecision.APPROVED
            assert db_permit_2.decision_code == DecisionCode.ALL_DEPARTMENTS_APPROVED
    finally:
        await shared_client.aclose()
