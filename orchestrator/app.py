"""
Permit Orchestrator - FastAPI Application (T1 Core Orchestrator).

Integrates parallel fan-out (dispatcher) and consensus engine to coordinate
multi-department permit evaluations with fail-safe consensus rules.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
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


class OrchestratorApp:
    """Encapsulates in-memory saga state storage and orchestrator dispatch logic."""

    def __init__(self):
        self.permits_db: Dict[str, PermitStatusResponse] = {}
        self.permit_events: Dict[str, asyncio.Event] = {}
        self.trace_events: Dict[str, List[TraceEvent]] = {}
        self.event_subscribers: Dict[str, List[asyncio.Queue[Optional[TraceEvent]]]] = {}
        self.custom_dispatcher: Optional[Callable[..., Any]] = None
        self.custom_consensus: Optional[Callable[..., Any]] = None

    def reset(self) -> None:
        self.permits_db.clear()
        self.permit_events.clear()
        self.trace_events.clear()
        self.event_subscribers.clear()

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

    def close_stream_subscribers(self, permit_id: str) -> None:
        """Signal completion to all active SSE subscribers by pushing None."""
        for q in self.event_subscribers.get(permit_id, []):
            try:
                q.put_nowait(None)
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

        return await dispatch_permit(permit=permit, trace_id=trace_id, on_event=on_event)

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


def create_app(
    dispatcher_override: Optional[Callable[..., Any]] = None,
    consensus_override: Optional[Callable[..., Any]] = None,
) -> FastAPI:
    """Create and configure the FastAPI orchestrator application."""
    app = FastAPI(title="Permit Orchestrator", version="1.0.0")

    # Issue 7: Add CORS Middleware for frontend (localhost:3000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    orchestrator_state = OrchestratorApp()
    if dispatcher_override is not None:
        orchestrator_state.custom_dispatcher = dispatcher_override
    if consensus_override is not None:
        orchestrator_state.custom_consensus = consensus_override

    app.state.orchestrator = orchestrator_state

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.post(
        "/submit",
        response_model=SubmitPermitResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Submit permit application",
    )
    async def submit_permit(
        permit: Permit,
        wait: bool = Query(
            False,
            description="Whether to await saga fan-out and consensus before returning",
        ),
    ):
        permit_id = permit.permit_id if permit.permit_id else str(uuid4())
        if permit.permit_id != permit_id:
            permit = permit.model_copy(update={"permit_id": permit_id})
        trace_id = str(uuid4())

        initial_status = PermitStatusResponse(
            permit_id=permit_id,
            trace_id=trace_id,
            state=SagaState.RECEIVED,
            final_decision=FinalDecision.PENDING,
            decision_code=DecisionCode.PENDING,
            created_at=datetime.now(timezone.utc),
            department_results={},
        )
        orchestrator_state.permits_db[permit_id] = initial_status
        event = asyncio.Event()
        orchestrator_state.permit_events[permit_id] = event

        saga_coro = orchestrator_state.run_saga(permit_id, trace_id, permit)
        if wait:
            await saga_coro
        else:
            asyncio.create_task(saga_coro)

        return SubmitPermitResponse(
            permit_id=permit_id,
            trace_id=trace_id,
            state=SagaState.RECEIVED,
            stream_url=f"/permits/{permit_id}/stream",
        )

    @app.get(
        "/permits/{permit_id}",
        response_model=PermitStatusResponse,
        summary="Get permit status and final decision",
    )
    async def get_permit_status(
        permit_id: str,
        wait: bool = Query(
            False,
            description="If True, awaits saga completion if permit is currently evaluating",
        ),
    ):
        if permit_id not in orchestrator_state.permits_db:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permit '{permit_id}' not found",
            )

        if wait and permit_id in orchestrator_state.permit_events:
            await orchestrator_state.permit_events[permit_id].wait()

        return orchestrator_state.permits_db[permit_id]

    @app.get(
        "/permits/{permit_id}/stream",
        summary="Stream permit progress and decision events (SSE)",
    )
    async def stream_permit(permit_id: str):
        if permit_id not in orchestrator_state.permits_db:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permit '{permit_id}' not found",
            )

        queue: asyncio.Queue[Optional[TraceEvent]] = asyncio.Queue()
        if permit_id not in orchestrator_state.event_subscribers:
            orchestrator_state.event_subscribers[permit_id] = []
        orchestrator_state.event_subscribers[permit_id].append(queue)

        async def event_generator():
            try:
                # 1. Replay past trace events
                past_events = list(orchestrator_state.trace_events.get(permit_id, []))
                for ev in past_events:
                    yield f"event: trace_event\ndata: {ev.model_dump_json()}\n\n"

                status_item = orchestrator_state.permits_db[permit_id]
                # 2. If already decided, emit done event and finish
                if status_item.state == SagaState.DECIDED:
                    done_payload = StreamDonePayload(
                        permit_id=permit_id, final_decision=status_item.final_decision
                    )
                    yield f"event: done\ndata: {done_payload.model_dump_json()}\n\n"
                    return

                # 3. Stream live events until saga ends
                while True:
                    event_item = await queue.get()
                    if event_item is None:
                        current_status = orchestrator_state.permits_db[permit_id]
                        done_payload = StreamDonePayload(
                            permit_id=permit_id, final_decision=current_status.final_decision
                        )
                        yield f"event: done\ndata: {done_payload.model_dump_json()}\n\n"
                        break
                    yield f"event: trace_event\ndata: {event_item.model_dump_json()}\n\n"
            finally:
                if permit_id in orchestrator_state.event_subscribers:
                    try:
                        orchestrator_state.event_subscribers[permit_id].remove(queue)
                    except ValueError:
                        pass

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.post(
        "/admin/fault",
        response_model=FaultControlResponse,
        summary="Inject fault into a target department",
    )
    async def admin_set_fault(req: FaultControlRequest):
        if not req.department:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Department is required for admin fault injection",
            )

        endpoints = get_default_endpoints()
        dept_url = endpoints.get(req.department.value)
        if not dept_url:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown department '{req.department.value}'",
            )

        fault_url = resolve_endpoint_url(dept_url).replace("/evaluate", "/fault")
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.post(fault_url, json=req.model_dump(mode="json"))
                resp.raise_for_status()
                return FaultControlResponse.model_validate(resp.json())
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=exc.response.status_code,
                    detail=f"Department fault injection failed: {exc.response.text}",
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to communicate with department service: {exc}",
                )

    @app.get(
        "/admin/fault",
        response_model=AdminFaultResponse,
        summary="Get active fault configuration for all departments",
    )
    async def admin_get_fault():
        endpoints = get_default_endpoints()
        results: Dict[str, FaultControlResponse] = {}

        async with httpx.AsyncClient(timeout=3.0) as client:
            for dept in DepartmentName:
                dept_url = endpoints.get(dept.value)
                if not dept_url:
                    results[dept.value] = FaultControlResponse(
                        department=dept, mode=FaultControlRequest().mode, message="Endpoint unconfigured"
                    )
                    continue

                fault_url = resolve_endpoint_url(dept_url).replace("/evaluate", "/fault")
                try:
                    resp = await client.get(fault_url)
                    if resp.status_code == 200:
                        results[dept.value] = FaultControlResponse.model_validate(resp.json())
                    else:
                        results[dept.value] = FaultControlResponse(
                            department=dept, mode=FaultControlRequest().mode, message=f"HTTP {resp.status_code}"
                        )
                except Exception as exc:
                    results[dept.value] = FaultControlResponse(
                        department=dept, mode=FaultControlRequest().mode, message=f"Offline / Unreachable: {exc}"
                    )

        return AdminFaultResponse(
            zoning=results["zoning"],
            fire=results["fire"],
            water=results["water"],
            structural=results["structural"],
        )

    return app


app = create_app()
