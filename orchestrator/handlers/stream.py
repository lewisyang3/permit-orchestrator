"""
Permit Orchestrator - SSE Stream & Live Browser Handler (T5).

Provides:
- GET /permits/{permit_id}/stream: Server-Sent Events (SSE) with replay guarantee and done event.
- GET /permits/{permit_id}: Permit status and final consensus from SQLite.
- GET / and GET /live: Interactive browser dashboard for submitting permits and streaming events live.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Union
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse

from contract.models import (
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    FinalDecision,
    PermitStatusResponse,
    SagaState,
    SpanId,
    StreamDonePayload,
    TraceEvent,
    TraceEventType,
)
from orchestrator.handlers.submit import (
    EventBroadcaster,
    broadcaster,
    get_broadcaster,
)


def _lookup_permit(permit_id: str, request: Optional[Request] = None) -> Optional[PermitStatusResponse]:
    """Retrieve permit status from SQLite db or in-memory fallback."""
    orchestrator_state = None
    if request and hasattr(request, "app"):
        orchestrator_state = getattr(request.app.state, "orchestrator", None)
    if orchestrator_state is None:
        try:
            from orchestrator.app import get_active_app
            active_app = get_active_app()
            if active_app and hasattr(active_app.state, "orchestrator"):
                orchestrator_state = active_app.state.orchestrator
        except Exception:
            pass

    # If in-flight in orchestrator_state, prefer active in-flight status
    if orchestrator_state and hasattr(orchestrator_state, "permits_db"):
        mem_record = orchestrator_state.permits_db.get(permit_id)
        if mem_record is not None and mem_record.state != SagaState.DECIDED:
            return mem_record

    db_path = getattr(request.app.state, "db_path", None) if request and hasattr(request, "app") else None
    # 1. Query SQLite db if available
    try:
        from orchestrator.db import get_permit
        record = get_permit(permit_id, db_path=db_path)
        if record is not None:
            return record
    except Exception:
        pass

    # 2. Check app.state.orchestrator.permits_db
    if orchestrator_state and hasattr(orchestrator_state, "permits_db"):
        record = orchestrator_state.permits_db.get(permit_id)
        if record is not None:
            return record

    return None


def _lookup_trace_events(permit_id: str, request: Optional[Request] = None) -> List[TraceEvent]:
    """Retrieve all past trace events from SQLite db and in-memory store."""
    events: List[TraceEvent] = []
    seen_ids: Set[str] = set()

    db_path = getattr(request.app.state, "db_path", None) if request and hasattr(request, "app") else None
    # 1. Query SQLite db
    try:
        from orchestrator.db import get_trace_events
        db_events = get_trace_events(permit_id, db_path=db_path)
        if db_events:
            for ev in db_events:
                if ev.event_id not in seen_ids:
                    seen_ids.add(ev.event_id)
                    events.append(ev)
    except Exception:
        pass

    # 2. Check app.state.orchestrator.trace_events
    orchestrator_state = None
    if request and hasattr(request, "app"):
        orchestrator_state = getattr(request.app.state, "orchestrator", None)
    if orchestrator_state is None:
        try:
            from orchestrator.app import get_active_app
            active_app = get_active_app()
            if active_app and hasattr(active_app.state, "orchestrator"):
                orchestrator_state = active_app.state.orchestrator
        except Exception:
            pass

    if orchestrator_state and hasattr(orchestrator_state, "trace_events"):
        mem_events = orchestrator_state.trace_events.get(permit_id, [])
        for ev in mem_events:
            if ev.event_id not in seen_ids:
                seen_ids.add(ev.event_id)
                events.append(ev)

    return events


router = APIRouter(tags=["stream"])


@router.get(
    "/permits/{permit_id}/stream",
    summary="Stream permit progress and decision events (SSE)",
)
async def stream_permit(permit_id: str, request: Request = None):
    """
    Server-Sent Events (SSE) endpoint with replay guarantee.
    Streams recorded past TraceEvents from SQLite first, then streams live events
    from the event queue, and emits 'event: done' upon saga completion.
    """
    permit_status = _lookup_permit(permit_id, request)
    if permit_status is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permit '{permit_id}' not found",
        )

    active_broadcaster = get_broadcaster(request.app if request and hasattr(request, "app") else None)
    queue = active_broadcaster.subscribe(permit_id)

    async def event_generator():
        seen_event_ids: Set[str] = set()
        try:
            # 1. Snapshot past events and state together before yielding
            past_events = _lookup_trace_events(permit_id, request)
            status_at_snapshot = _lookup_permit(permit_id, request)
            decided_at_snapshot = (
                status_at_snapshot is not None
                and status_at_snapshot.state == SagaState.DECIDED
            )

            for ev in past_events:
                if ev.event_id not in seen_event_ids:
                    seen_event_ids.add(ev.event_id)
                    yield f"event: trace_event\ndata: {ev.model_dump_json()}\n\n"

            # 2. If already decided at snapshot time, emit done event and finish
            if decided_at_snapshot:
                done_payload = StreamDonePayload(
                    permit_id=permit_id,
                    final_decision=status_at_snapshot.final_decision,
                )
                yield f"event: done\ndata: {done_payload.model_dump_json()}\n\n"
                return

            # 3. Stream live events until saga ends
            while True:
                if request is not None and await request.is_disconnected():
                    break

                item = await queue.get()

                if item is None:
                    # Saga completed sentinel
                    current_status = _lookup_permit(permit_id, request)
                    final_dec = (
                        current_status.final_decision
                        if current_status
                        else FinalDecision.PENDING
                    )
                    done_payload = StreamDonePayload(
                        permit_id=permit_id,
                        final_decision=final_dec,
                    )
                    yield f"event: done\ndata: {done_payload.model_dump_json()}\n\n"
                    break

                if isinstance(item, StreamDonePayload):
                    yield f"event: done\ndata: {item.model_dump_json()}\n\n"
                    break

                if isinstance(item, dict):
                    if "final_decision" in item and "type" not in item:
                        done_payload = StreamDonePayload.model_validate(item)
                        yield f"event: done\ndata: {done_payload.model_dump_json()}\n\n"
                        break
                    else:
                        item = TraceEvent.model_validate(item)

                if isinstance(item, TraceEvent):
                    if item.event_id not in seen_event_ids:
                        seen_event_ids.add(item.event_id)
                        yield f"event: trace_event\ndata: {item.model_dump_json()}\n\n"

                    if item.type == TraceEventType.PERMIT_DECIDED:
                        # Permit decided event: emit done event
                        current_status = _lookup_permit(permit_id, request)
                        final_dec = (
                            current_status.final_decision
                            if current_status
                            else FinalDecision.PENDING
                        )
                        done_payload = StreamDonePayload(
                            permit_id=permit_id,
                            final_decision=final_dec,
                        )
                        yield f"event: done\ndata: {done_payload.model_dump_json()}\n\n"
                        break
        finally:
            active_broadcaster.unsubscribe(permit_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/permits/{permit_id}",
    response_model=PermitStatusResponse,
    summary="Get permit status and final decision",
)
async def get_permit_status(
    permit_id: str,
    request: Request = None,
    wait: bool = Query(
        False,
        description="If True, awaits saga completion if permit is currently evaluating",
    ),
):
    """Query permit status and department results from SQLite."""
    status_item = _lookup_permit(permit_id, request)
    if status_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Permit '{permit_id}' not found",
        )

    if wait and status_item.state != SagaState.DECIDED:
        event = None
        if request and hasattr(request, "app"):
            orchestrator_state = getattr(request.app.state, "orchestrator", None)
            if orchestrator_state and hasattr(orchestrator_state, "permit_events"):
                event = orchestrator_state.permit_events.get(permit_id)
        if event is None:
            try:
                from orchestrator.handlers.submit import get_permit_event
                event = get_permit_event(permit_id)
            except ImportError:
                pass

        if event:
            try:
                await asyncio.wait_for(event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            status_item = _lookup_permit(permit_id, request)
        else:
            # Poll SQLite database for up to 5 seconds
            for _ in range(50):
                await asyncio.sleep(0.1)
                status_item = _lookup_permit(permit_id, request)
                if status_item and status_item.state == SagaState.DECIDED:
                    break

    return status_item


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Permit Orchestrator - Live Browser Saga Stream</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --card-border: #1f2937;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --green: #10b981;
      --red: #ef4444;
      --amber: #f59e0b;
      --purple: #8b5cf6;
      --cyan: #06b6d4;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 24px;
      line-height: 1.5;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 24px;
      flex-wrap: wrap;
      gap: 12px;
    }
    .title-group h1 {
      font-size: 1.6rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
      color: #fff;
    }
    .title-group p {
      color: var(--text-muted);
      font-size: 0.9rem;
      margin-top: 4px;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: #1e293b;
      border: 1px solid #334155;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      letter-spacing: 0.03em;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #64748b;
    }
    .dot.live {
      background: var(--green);
      box-shadow: 0 0 10px var(--green);
      animation: pulse 1.5s infinite;
    }
    .dot.completed { background: var(--cyan); }
    .dot.error { background: var(--red); }
    @keyframes pulse {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.1); }
      100% { opacity: 1; transform: scale(1); }
    }
    .grid {
      display: grid;
      grid-template-columns: 1.1fr 1fr;
      gap: 20px;
      margin-bottom: 24px;
    }
    @media (max-width: 1024px) {
      .grid { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
    }
    .card-title {
      font-size: 1.05rem;
      font-weight: 600;
      color: #fff;
      margin-bottom: 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .form-group {
      margin-bottom: 14px;
    }
    label {
      display: block;
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 6px;
      font-weight: 500;
    }
    select, textarea, input {
      width: 100%;
      background: #0d131f;
      border: 1px solid #2d3748;
      border-radius: 8px;
      color: #e2e8f0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.85rem;
      padding: 10px 12px;
      outline: none;
      transition: border-color 0.2s;
    }
    select:focus, textarea:focus, input:focus {
      border-color: var(--accent);
    }
    textarea {
      height: 190px;
      resize: vertical;
    }
    .btn-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    button {
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 10px 18px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: background 0.2s, transform 0.1s;
    }
    button:hover { background: var(--accent-hover); }
    button:active { transform: scale(0.98); }
    button.secondary {
      background: #1f2937;
      color: #cbd5e1;
      border: 1px solid #374151;
    }
    button.secondary:hover { background: #374151; }
    .consensus-banner {
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      text-align: center;
      transition: all 0.3s;
    }
    .consensus-banner.APPROVED {
      background: rgba(16, 185, 129, 0.12);
      border-color: var(--green);
    }
    .consensus-banner.REJECTED {
      background: rgba(239, 68, 68, 0.12);
      border-color: var(--red);
    }
    .decision-label {
      font-size: 1.4rem;
      font-weight: 800;
      letter-spacing: 0.05em;
    }
    .decision-label.APPROVED { color: var(--green); }
    .decision-label.REJECTED { color: var(--red); }
    .decision-label.PENDING { color: var(--amber); }
    .consensus-meta {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: 6px;
    }
    .departments-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
    }
    .dept-card {
      background: #0d131f;
      border: 1px solid #1f2937;
      border-radius: 8px;
      padding: 12px;
    }
    .dept-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.9rem;
      font-weight: 600;
      margin-bottom: 6px;
    }
    .dept-badge {
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 9999px;
      font-weight: 700;
    }
    .badge-PENDING { background: #334155; color: #94a3b8; }
    .badge-APPROVED { background: rgba(16, 185, 129, 0.2); color: var(--green); }
    .badge-REJECTED { background: rgba(239, 68, 68, 0.2); color: var(--red); }
    .badge-TIMEOUT { background: rgba(245, 158, 11, 0.2); color: var(--amber); }
    .badge-ERROR { background: rgba(239, 68, 68, 0.2); color: var(--red); }
    .dept-stats {
      font-size: 0.78rem;
      color: var(--text-muted);
      display: flex;
      gap: 10px;
    }
    .dept-reasons {
      margin-top: 6px;
      font-size: 0.75rem;
      color: #cbd5e1;
      max-height: 50px;
      overflow-y: auto;
    }
    .trace-section {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 20px;
    }
    .trace-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      flex-wrap: wrap;
      gap: 10px;
    }
    .trace-list {
      background: #090d16;
      border: 1px solid #1a2234;
      border-radius: 8px;
      max-height: 420px;
      overflow-y: auto;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.8rem;
    }
    .trace-item {
      padding: 8px 12px;
      border-bottom: 1px solid #151d2f;
      display: flex;
      align-items: flex-start;
      gap: 12px;
      transition: background 0.15s;
    }
    .trace-item:hover { background: #0f172a; }
    .trace-time { color: #64748b; white-space: nowrap; font-size: 0.75rem; }
    .span-pill {
      font-size: 0.72rem;
      padding: 1px 6px;
      border-radius: 4px;
      font-weight: 600;
      white-space: nowrap;
    }
    .span-orchestrator { background: rgba(139, 92, 246, 0.2); color: var(--purple); }
    .span-dept-zoning { background: rgba(59, 130, 246, 0.2); color: var(--accent); }
    .span-dept-fire { background: rgba(245, 158, 11, 0.2); color: var(--amber); }
    .span-dept-water { background: rgba(6, 182, 212, 0.2); color: var(--cyan); }
    .span-dept-structural { background: rgba(16, 185, 129, 0.2); color: var(--green); }
    .span-consensus { background: rgba(236, 72, 153, 0.2); color: #ec4899; }
    .trace-type {
      font-weight: 600;
      white-space: nowrap;
    }
    .type-PERMIT_RECEIVED { color: var(--purple); }
    .type-CALL_DISPATCHED { color: var(--accent); }
    .type-CALL_SUCCEEDED { color: var(--green); }
    .type-CALL_RETRYING { color: var(--amber); }
    .type-CALL_TIMED_OUT, .type-CALL_FAILED { color: var(--red); }
    .type-CONSENSUS_STARTED { color: #ec4899; }
    .type-PERMIT_DECIDED, .type-done { color: var(--green); }
    .trace-details {
      color: #94a3b8;
      word-break: break-all;
      flex: 1;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="title-group">
      <h1>⚡ Permit Orchestrator Live Stream</h1>
      <p>Server-Sent Events replay & live multi-department consensus viewing</p>
    </div>
    <div class="status-badge">
      <span class="dot" id="streamDot"></span>
      <span id="streamStatusText">IDLE</span>
    </div>
  </div>

  <div class="grid">
    <!-- Left Column: Submit & Connect Form -->
    <div class="card">
      <div class="card-title">
        <span>Submit Application</span>
        <select id="presetSelect" style="width: auto; padding: 4px 8px;">
          <option value="approved">Fixture 1: Residential Rear Addition (Approved)</option>
          <option value="rejected_fire">Fixture 2: Bistro & Micro-Venue (Fire Rejected)</option>
          <option value="timeout">Fixture 3: Storage Warehouse (Offline / Timeout)</option>
        </select>
      </div>
      <div class="form-group">
        <label for="permitJson">Permit JSON Payload</label>
        <textarea id="permitJson" spellcheck="false"></textarea>
      </div>
      <div class="btn-row">
        <button id="btnSubmit">🚀 Submit & Stream Live</button>
        <button id="btnConnectExisting" class="secondary">🔄 Connect by ID</button>
      </div>
      <div class="form-group" style="margin-top: 14px;">
        <label for="existingIdInput">Stream URL / Permit ID</label>
        <input type="text" id="existingIdInput" placeholder="e.g. pm-fixture-approved-001 or UUID">
      </div>
    </div>

    <!-- Right Column: Consensus Outcome & Departments Matrix -->
    <div class="card">
      <div class="card-title">
        <span>Consensus Outcome</span>
        <span id="sagaStateBadge" style="font-size: 0.8rem; color: var(--text-muted);">SAGA: IDLE</span>
      </div>
      <div class="consensus-banner" id="consensusBanner">
        <div class="decision-label PENDING" id="decisionLabel">NO PERMIT LOADED</div>
        <div class="consensus-meta" id="consensusMeta">Submit a permit or connect an existing stream to view live trace.</div>
      </div>
      <div class="departments-grid">
        <div class="dept-card" id="cardZoning">
          <div class="dept-header">
            <span>Zoning</span>
            <span class="dept-badge badge-PENDING" id="badgeZoning">PENDING</span>
          </div>
          <div class="dept-stats"><span id="statsZoning">0 ms · 0 attempts</span></div>
          <div class="dept-reasons" id="reasonsZoning">Awaiting review...</div>
        </div>
        <div class="dept-card" id="cardFire">
          <div class="dept-header">
            <span>Fire</span>
            <span class="dept-badge badge-PENDING" id="badgeFire">PENDING</span>
          </div>
          <div class="dept-stats"><span id="statsFire">0 ms · 0 attempts</span></div>
          <div class="dept-reasons" id="reasonsFire">Awaiting review...</div>
        </div>
        <div class="dept-card" id="cardWater">
          <div class="dept-header">
            <span>Water</span>
            <span class="dept-badge badge-PENDING" id="badgeWater">PENDING</span>
          </div>
          <div class="dept-stats"><span id="statsWater">0 ms · 0 attempts</span></div>
          <div class="dept-reasons" id="reasonsWater">Awaiting review...</div>
        </div>
        <div class="dept-card" id="cardStructural">
          <div class="dept-header">
            <span>Structural</span>
            <span class="dept-badge badge-PENDING" id="badgeStructural">PENDING</span>
          </div>
          <div class="dept-stats"><span id="statsStructural">0 ms · 0 attempts</span></div>
          <div class="dept-reasons" id="reasonsStructural">Awaiting review...</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Bottom Section: Live Distributed Trace Event Waterfall -->
  <div class="trace-section">
    <div class="trace-toolbar">
      <div style="font-weight: 600; font-size: 1.05rem;">
        <span>Distributed Trace Events</span>
        <span id="eventCount" style="font-size: 0.85rem; color: var(--text-muted); margin-left: 8px;">(0 events)</span>
      </div>
      <div style="display: flex; gap: 10px; align-items: center;">
        <label style="margin: 0; font-size: 0.8rem; display: flex; align-items: center; gap: 4px; cursor: pointer;">
          <input type="checkbox" id="autoScroll" checked style="width: auto;"> Auto-scroll
        </label>
        <button id="btnClearTrace" class="secondary" style="padding: 6px 12px; font-size: 0.8rem;">Clear Feed</button>
      </div>
    </div>
    <div class="trace-list" id="traceList">
      <div class="trace-item" style="color: var(--text-muted); justify-content: center; padding: 24px;">
        No events received yet. Submit an application to start streaming.
      </div>
    </div>
  </div>

  <script>
    const PRESETS = {
      approved: {
        "permit_id": "pm-fixture-approved-001",
        "project_title": "Single Family Residential Rear Addition",
        "project_description": "Ground floor bedroom and bathroom expansion adhering to standard setbacks and utility connections.",
        "estimated_cost_usd": 85000,
        "property_address": { "street": "1428 Elm Street", "city": "Springfield", "state": "CA", "zip": "94016" },
        "applicant": { "name": "Alex Rivera", "role": "licensed_contractor", "email": "alex.rivera@buildsmart.example.com" },
        "zoning": { "zone_code": "R-1", "building_height_ft": 24.5, "lot_area_sqft": 7500, "building_footprint_sqft": 2600, "setback_front_ft": 25.0, "setback_rear_ft": 20.0, "setback_side_ft": 8.0 },
        "fire": { "occupancy_type": "residential", "occupant_load": 6, "has_sprinkler_system": false, "egress_exits_count": 2, "distance_to_hydrant_ft": 180, "has_fire_alarm": true },
        "water": { "water_meter_size_inches": 1.0, "daily_water_usage_gpd": 320, "has_backflow_preventer": true, "has_stormwater_plan": true },
        "structural": { "stories_count": 2, "construction_type": "wood_frame", "seismic_design_category": "C", "foundation_type": "continuous_spread_footing" }
      },
      rejected_fire: {
        "permit_id": "pm-fixture-rejected-fire-002",
        "project_title": "Downtown Community Bistro & Micro-Venue",
        "project_description": "Interior conversion of existing commercial retail unit into assembly space with bar and performance stage.",
        "estimated_cost_usd": 210000,
        "property_address": { "street": "404 Market Street", "city": "Springfield", "state": "CA", "zip": "94016" },
        "applicant": { "name": "Jordan Chen", "role": "architect", "email": "jordan@urbanarch.example.com" },
        "zoning": { "zone_code": "C-2", "building_height_ft": 32.0, "lot_area_sqft": 12000, "building_footprint_sqft": 6500, "setback_front_ft": 0.0, "setback_rear_ft": 10.0, "setback_side_ft": 0.0 },
        "fire": { "occupancy_type": "assembly", "occupant_load": 110, "has_sprinkler_system": false, "egress_exits_count": 1, "distance_to_hydrant_ft": 250, "has_fire_alarm": true },
        "water": { "water_meter_size_inches": 2.0, "daily_water_usage_gpd": 1800, "has_backflow_preventer": true, "has_stormwater_plan": true },
        "structural": { "stories_count": 2, "construction_type": "reinforced_concrete", "seismic_design_category": "D", "foundation_type": "slab_on_grade" }
      },
      timeout: {
        "permit_id": "pm-fixture-timeout-offline-003",
        "project_title": "Suburban Storage Warehouse Modernization",
        "project_description": "Structural reinforcement and modern racking installation. Used to trigger and verify department offline timeout and retry handling.",
        "estimated_cost_usd": 140000,
        "property_address": { "street": "88 Industrial Way", "city": "Springfield", "state": "CA", "zip": "94016" },
        "applicant": { "name": "Morgan Davis", "role": "engineer", "email": "m.davis@norcalengineering.example.com" },
        "zoning": { "zone_code": "M-1", "building_height_ft": 36.0, "lot_area_sqft": 45000, "building_footprint_sqft": 18000, "setback_front_ft": 35.0, "setback_rear_ft": 25.0, "setback_side_ft": 20.0 },
        "fire": { "occupancy_type": "industrial", "occupant_load": 25, "has_sprinkler_system": true, "egress_exits_count": 4, "distance_to_hydrant_ft": 120, "has_fire_alarm": true },
        "water": { "water_meter_size_inches": 2.0, "daily_water_usage_gpd": 600, "has_backflow_preventer": true, "has_stormwater_plan": true },
        "structural": { "stories_count": 1, "construction_type": "light_steel", "seismic_design_category": "C", "foundation_type": "slab_on_grade" }
      }
    };

    let activeEventSource = null;
    let eventCounter = 0;

    const permitJsonEl = document.getElementById('permitJson');
    const presetSelect = document.getElementById('presetSelect');
    const streamDot = document.getElementById('streamDot');
    const streamStatusText = document.getElementById('streamStatusText');
    const traceList = document.getElementById('traceList');
    const eventCountEl = document.getElementById('eventCount');
    const decisionLabel = document.getElementById('decisionLabel');
    const consensusMeta = document.getElementById('consensusMeta');
    const consensusBanner = document.getElementById('consensusBanner');
    const sagaStateBadge = document.getElementById('sagaStateBadge');

    function loadPreset(key) {
      if (PRESETS[key]) {
        permitJsonEl.value = JSON.stringify(PRESETS[key], null, 2);
        document.getElementById('existingIdInput').value = PRESETS[key].permit_id;
      }
    }
    presetSelect.addEventListener('change', (e) => loadPreset(e.target.value));
    loadPreset('approved');

    function setStatus(state, text) {
      streamDot.className = 'dot ' + state;
      streamStatusText.innerText = text;
    }

    function resetUI() {
      eventCounter = 0;
      eventCountEl.innerText = '(0 events)';
      traceList.innerHTML = '';
      decisionLabel.className = 'decision-label PENDING';
      decisionLabel.innerText = 'EVALUATING...';
      consensusBanner.className = 'consensus-banner';
      consensusMeta.innerText = 'Streaming trace events in real time...';
      sagaStateBadge.innerText = 'SAGA: EVALUATING';

      ['Zoning', 'Fire', 'Water', 'Structural'].forEach(dept => {
        document.getElementById('badge' + dept).className = 'dept-badge badge-PENDING';
        document.getElementById('badge' + dept).innerText = 'PENDING';
        document.getElementById('stats' + dept).innerText = '0 ms · 0 attempts';
        document.getElementById('reasons' + dept).innerText = 'Evaluating...';
      });
    }

    function addTraceRow(ev) {
      if (eventCounter === 0) traceList.innerHTML = '';
      eventCounter++;
      eventCountEl.innerText = `(${eventCounter} events)`;

      const row = document.createElement('div');
      row.className = 'trace-item';

      const timeStr = typeof ev.ts === 'string' ? ev.ts.split('T')[1] || ev.ts : new Date().toISOString().split('T')[1];
      const spanClass = 'span-' + (ev.span || 'orchestrator').replace(':', '-');
      const typeClass = 'type-' + (ev.type || 'trace_event');

      let detailStr = '';
      if (ev.detail) {
        if (typeof ev.detail === 'string') detailStr = ev.detail;
        else detailStr = JSON.stringify(ev.detail);
      }

      row.innerHTML = `
        <span class="trace-time">${timeStr}</span>
        <span class="span-pill ${spanClass}">${ev.span || 'orchestrator'}</span>
        <span class="trace-type ${typeClass}">${ev.type || 'EVENT'}</span>
        <span class="trace-details">${detailStr}</span>
      `;

      traceList.appendChild(row);
      if (document.getElementById('autoScroll').checked) {
        traceList.scrollTop = traceList.scrollHeight;
      }
    }

    function updateDepartment(deptName, status, latencyMs, attempts, reasonMsg) {
      const formatted = deptName.charAt(0).toUpperCase() + deptName.slice(1);
      const badge = document.getElementById('badge' + formatted);
      const stats = document.getElementById('stats' + formatted);
      const reasons = document.getElementById('reasons' + formatted);
      if (badge) {
        badge.className = 'dept-badge badge-' + status;
        badge.innerText = status;
      }
      if (stats) stats.innerText = `${latencyMs ? latencyMs.toFixed(0) : 0} ms · ${attempts || 1} att`;
      if (reasons && reasonMsg) reasons.innerText = reasonMsg;
    }

    function connectSSE(url) {
      if (activeEventSource) {
        activeEventSource.close();
      }

      resetUI();
      setStatus('live', 'STREAMING');

      const es = new EventSource(url);
      activeEventSource = es;

      es.addEventListener('trace_event', (e) => {
        try {
          const ev = JSON.parse(e.data);
          addTraceRow(ev);

          // Update department cards based on events
          if (ev.span && ev.span.startsWith('dept:')) {
            const dept = ev.span.split(':')[1];
            if (ev.type === 'CALL_SUCCEEDED') {
              const status = ev.detail?.status || 'APPROVED';
              updateDepartment(dept, status, ev.detail?.latency_ms, ev.detail?.attempt, ev.detail?.reasons?.[0]?.message || 'Approved');
            } else if (ev.type === 'CALL_TIMED_OUT') {
              updateDepartment(dept, 'TIMEOUT', 2000, ev.detail?.attempt, 'Timed out after 2000 ms');
            } else if (ev.type === 'CALL_FAILED') {
              updateDepartment(dept, 'ERROR', ev.detail?.latency_ms, ev.detail?.attempt, ev.detail?.error || 'Call failed');
            } else if (ev.type === 'CALL_RETRYING') {
              updateDepartment(dept, 'PENDING', 0, ev.detail?.attempt, `Retrying (attempt ${ev.detail?.attempt})...`);
            }
          }

          if (ev.type === 'PERMIT_DECIDED') {
            const dec = ev.detail?.final_decision;
            if (dec) handleDone({ final_decision: dec, detail: ev.detail });
          }
        } catch (err) {
          console.error("Error parsing trace event", err);
        }
      });

      es.addEventListener('done', (e) => {
        try {
          const payload = JSON.parse(e.data);
          handleDone(payload);
        } catch (err) {
          console.error("Error parsing done event", err);
        }
        es.close();
        setStatus('completed', 'COMPLETED');
      });

      es.onerror = (err) => {
        setStatus('completed', 'COMPLETED');
        es.close();
      };
    }

    function handleDone(payload) {
      const dec = payload.final_decision;
      decisionLabel.className = 'decision-label ' + dec;
      decisionLabel.innerText = dec === 'APPROVED' ? '✅ PERMIT APPROVED' : '❌ PERMIT REJECTED';
      consensusBanner.className = 'consensus-banner ' + dec;
      sagaStateBadge.innerText = 'SAGA: DECIDED';
      if (payload.detail?.decision_summary) {
        consensusMeta.innerText = payload.detail.decision_summary;
      } else {
        consensusMeta.innerText = `Final Consensus: ${dec}`;
      }
    }

    document.getElementById('btnSubmit').addEventListener('click', async () => {
      try {
        const payload = JSON.parse(permitJsonEl.value);
        setStatus('live', 'SUBMITTING');
        const resp = await fetch('/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!resp.ok) {
          const err = await resp.text();
          alert('Submit failed: ' + err);
          setStatus('error', 'ERROR');
          return;
        }
        const data = await resp.json();
        document.getElementById('existingIdInput').value = data.permit_id;
        connectSSE(data.stream_url);
      } catch (e) {
        alert('Invalid JSON or request failed: ' + e);
        setStatus('error', 'ERROR');
      }
    });

    document.getElementById('btnConnectExisting').addEventListener('click', () => {
      let id = document.getElementById('existingIdInput').value.trim();
      if (!id) {
        alert('Please enter a permit ID or stream URL');
        return;
      }
      let url = id;
      if (!id.startsWith('/')) {
        url = `/permits/${id}/stream`;
      }
      connectSSE(url);
    });

    document.getElementById('btnClearTrace').addEventListener('click', () => {
      eventCounter = 0;
      eventCountEl.innerText = '(0 events)';
      traceList.innerHTML = '<div class="trace-item" style="color: var(--text-muted); justify-content: center; padding: 24px;">Feed cleared.</div>';
    });
  </script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, summary="Interactive live browser UI")
@router.get("/live", response_class=HTMLResponse, summary="Interactive live browser UI")
async def get_live_browser_page():
    """Serves an interactive HTML browser page connecting to SSE stream."""
    return HTMLResponse(content=HTML_PAGE, status_code=status.HTTP_200_OK)
