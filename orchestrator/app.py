"""
Permit Orchestrator - FastAPI Application (T1 Core Orchestrator).

Integrates parallel fan-out (dispatcher) and consensus engine to coordinate
multi-department permit evaluations with fail-safe consensus rules.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from contract.models import (
    AdminFaultResponse,
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    FaultControlRequest,
    FaultControlResponse,
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
from orchestrator.consensus import evaluate_consensus
from orchestrator.dispatcher import get_default_endpoints, resolve_endpoint_url
from orchestrator.handlers import admin_router, intake_router, stream_router, submit_router
from orchestrator.handlers.submit import broadcaster


class OrchestratorApp:
    """Encapsulates in-memory saga state storage and orchestrator dispatch logic."""

    def __init__(
        self,
        endpoints: Optional[Dict[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        backoff_delays: Optional[Sequence[float]] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.permits_db: Dict[str, PermitStatusResponse] = {}
        self.permit_events: Dict[str, asyncio.Event] = {}
        self.trace_events: Dict[str, List[TraceEvent]] = {}
        self.event_subscribers: Dict[str, List[asyncio.Queue[Optional[TraceEvent]]]] = {}
        self.background_tasks: set[asyncio.Task[Any]] = set()
        self.custom_dispatcher: Optional[Callable[..., Any]] = None
        self.custom_consensus: Optional[Callable[..., Any]] = None
        self.endpoints: Optional[Dict[str, str]] = endpoints
        self.http_client: Optional[httpx.AsyncClient] = http_client
        self.backoff_delays: Optional[Sequence[float]] = backoff_delays
        self.timeout_seconds: Optional[float] = timeout_seconds

    def reset(self) -> None:
        self.permits_db.clear()
        self.permit_events.clear()
        self.trace_events.clear()
        self.event_subscribers.clear()
        self.background_tasks.clear()

    def record_event(self, permit_id: str, event: TraceEvent) -> None:
        """Store trace event and dispatch to any active SSE subscribers."""
        if permit_id not in self.trace_events:
            self.trace_events[permit_id] = []
        self.trace_events[permit_id].append(event)

        for q in self.event_subscribers.get(permit_id, []):
            try:
                q.put_nowait(event)
            except Exception:
                pass

        try:
            from orchestrator.handlers.submit import broadcaster
            broadcaster.publish(permit_id, event)
        except Exception:
            pass

    def close_stream_subscribers(self, permit_id: str) -> None:
        """Signal completion to all active SSE subscribers by pushing None."""
        for q in self.event_subscribers.get(permit_id, []):
            try:
                q.put_nowait(None)
            except Exception:
                pass

        try:
            from orchestrator.handlers.submit import broadcaster
            broadcaster.close(permit_id)
        except Exception:
            pass

    async def call_dispatcher(
        self, permit: Permit, trace_id: str, on_event: Optional[Callable[[TraceEvent], Any]] = None
    ) -> Dict[str, DepartmentResultSummary]:
        # 1. Custom dispatcher if explicitly set
        if self.custom_dispatcher is not None:
            sig = inspect.signature(self.custom_dispatcher)
            kwargs = {}
            if "permit" in sig.parameters:
                kwargs["permit"] = permit
            if "trace_id" in sig.parameters:
                kwargs["trace_id"] = trace_id
            if "on_event" in sig.parameters:
                kwargs["on_event"] = on_event

            if len(kwargs) == len(sig.parameters):
                res = self.custom_dispatcher(**kwargs)
            else:
                try:
                    res = self.custom_dispatcher(permit, trace_id, on_event=on_event)
                except TypeError:
                    try:
                        res = self.custom_dispatcher(permit, trace_id)
                    except TypeError:
                        res = self.custom_dispatcher(permit)

            if inspect.isawaitable(res):
                return await res
            return res

        # 2. Use orchestrator.dispatcher
        from orchestrator.dispatcher import dispatch_permit

        kwargs: Dict[str, Any] = {
            "permit": permit,
            "trace_id": trace_id,
            "on_event": on_event,
        }
        if self.endpoints is not None:
            kwargs["endpoints"] = self.endpoints
        if self.http_client is not None:
            kwargs["client"] = self.http_client
        if self.backoff_delays is not None:
            kwargs["backoff_delays"] = self.backoff_delays
        if self.timeout_seconds is not None:
            kwargs["timeout_seconds"] = self.timeout_seconds

        return await dispatch_permit(**kwargs)

    def call_consensus(
        self, department_results: Dict[str, DepartmentResultSummary]
    ) -> Tuple[FinalDecision, DecisionCode, str, List[dict]]:
        if self.custom_consensus is not None:
            return self.custom_consensus(department_results)
        return evaluate_consensus(department_results)

    async def run_saga(self, permit_id: str, trace_id: str, permit: Permit) -> None:
        event = self.permit_events.get(permit_id)
        start_time = datetime.now(timezone.utc)
        try:
            # 1. Record root span PERMIT_RECEIVED event
            self.record_event(
                permit_id,
                TraceEvent(
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=SpanId.ORCHESTRATOR,
                    parent_span=None,
                    type=TraceEventType.PERMIT_RECEIVED,
                    ts=start_time,
                    detail={
                        "project_title": permit.project_title,
                        "applicant": permit.applicant.name if permit.applicant else "Unknown",
                        "state": SagaState.RECEIVED.value,
                    },
                ),
            )

            if permit_id in self.permits_db:
                self.permits_db[permit_id].state = SagaState.EVALUATING

            # 2. Dispatch to departments with on_event callback
            on_event = lambda ev: self.record_event(permit_id, ev)
            department_results = await self.call_dispatcher(permit, trace_id, on_event=on_event)

            # 3. Record CONSENSUS_STARTED event
            self.record_event(
                permit_id,
                TraceEvent(
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=SpanId.CONSENSUS,
                    parent_span="orchestrator",
                    type=TraceEventType.CONSENSUS_STARTED,
                    ts=datetime.now(timezone.utc),
                    detail={
                        "departments_received": len(department_results),
                    },
                ),
            )

            # 4. Evaluate consensus
            final_decision, decision_code, summary, violations = self.call_consensus(
                department_results
            )

            decided_time = datetime.now(timezone.utc)
            if permit_id in self.permits_db:
                status_record = self.permits_db[permit_id]
                status_record.department_results = department_results
                status_record.final_decision = final_decision
                status_record.decision_code = decision_code
                status_record.decision_summary = summary
                status_record.decided_at = decided_time
                status_record.state = SagaState.DECIDED

            # 5. Record root span PERMIT_DECIDED event
            self.record_event(
                permit_id,
                TraceEvent(
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=SpanId.ORCHESTRATOR,
                    parent_span=None,
                    type=TraceEventType.PERMIT_DECIDED,
                    ts=decided_time,
                    detail={
                        "state": SagaState.DECIDED.value,
                        "final_decision": final_decision.value,
                        "decision_code": decision_code.value,
                        "decision_summary": summary,
                        "violations": violations,
                    },
                ),
            )

        except Exception as exc:
            # Fail-safe consensus recovery on any unhandled failure (Goal #1)
            decided_time = datetime.now(timezone.utc)
            err_summary = f"Saga execution failed: {exc}"
            if permit_id in self.permits_db:
                status_record = self.permits_db[permit_id]
                status_record.final_decision = FinalDecision.REJECTED
                status_record.decision_code = DecisionCode.INCOMPLETE_REVIEWS
                status_record.decision_summary = err_summary
                status_record.decided_at = decided_time
                status_record.state = SagaState.DECIDED

            self.record_event(
                permit_id,
                TraceEvent(
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=SpanId.ORCHESTRATOR,
                    parent_span=None,
                    type=TraceEventType.PERMIT_DECIDED,
                    ts=decided_time,
                    detail={
                        "state": SagaState.DECIDED.value,
                        "final_decision": FinalDecision.REJECTED.value,
                        "decision_code": DecisionCode.INCOMPLETE_REVIEWS.value,
                        "decision_summary": err_summary,
                        "violations": [],
                    },
                ),
            )
        finally:
            self.close_stream_subscribers(permit_id)
            if event is not None:
                event.set()


_active_app: Optional[FastAPI] = None


def get_active_app() -> Optional[FastAPI]:
    return _active_app


def create_app(
    dispatcher_override: Optional[Callable[..., Any]] = None,
    consensus_override: Optional[Callable[..., Any]] = None,
    endpoints: Optional[Dict[str, str]] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    backoff_delays: Optional[Sequence[float]] = None,
    timeout_seconds: Optional[float] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> FastAPI:
    """Create and configure the FastAPI orchestrator application."""
    app = FastAPI(title="Permit Orchestrator", version="1.0.0")
    global _active_app
    _active_app = app

    # Issue 7: Add CORS Middleware for frontend (localhost:3000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    orchestrator_state = OrchestratorApp(
        endpoints=endpoints,
        http_client=http_client,
        backoff_delays=backoff_delays,
        timeout_seconds=timeout_seconds,
    )
    if dispatcher_override is not None:
        orchestrator_state.custom_dispatcher = dispatcher_override
    if consensus_override is not None:
        orchestrator_state.custom_consensus = consensus_override

    app.state.orchestrator = orchestrator_state
    app.state.dispatcher_override = dispatcher_override
    app.state.consensus_override = consensus_override
    app.state.db_path = db_path

    app.state.broadcaster = broadcaster

    # Initialize SQLite database
    try:
        from orchestrator import db
        db.init_db(db_path)
    except Exception:
        pass

    app.include_router(admin_router)
    app.include_router(submit_router)
    app.include_router(intake_router)
    app.include_router(stream_router)

    # Flatten included routes onto app.routes for direct route inspection compatibility
    for router_item in (admin_router, submit_router, stream_router, intake_router):
        for route in router_item.routes:
            if route not in app.routes:
                app.routes.append(route)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


app = create_app()
