"""
Permit Submission Handler (T5).

Handles POST /submit:
- Validates Permit payload (from contract.models.Permit)
- Persists initial permit record to SQLite (via orchestrator/db.py) with SagaState.RECEIVED
- Emits root TraceEvent (TraceEventType.PERMIT_RECEIVED) to SQLite and event broadcaster
- Spawns asynchronous background saga:
  - Dispatches permit evaluation across 4 departments (orchestrator/dispatcher.py)
  - Records TraceEvent to SQLite (append-only) and publishes to in-memory broadcast channel/queues for live SSE streaming
  - Runs consensus evaluation (orchestrator/consensus.py)
  - Updates permit record in SQLite with final_decision, decision_code, and decided_at
  - Persists department results in SQLite
  - Emits TraceEventType.PERMIT_DECIDED to SQLite and notifies SSE subscribers
- Returns HTTP 202 Accepted with SubmitPermitResponse
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from uuid import uuid4

from fastapi import APIRouter, Query, Request, status

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
    SubmitPermitResponse,
    TraceEvent,
    TraceEventType,
)
from orchestrator.consensus import evaluate_consensus
from orchestrator.dispatcher import dispatch_permit

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """In-memory event broadcaster allowing SSE subscribers to receive live TraceEvents."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[asyncio.Queue[Optional[TraceEvent]]]] = {}
        self._closed_permits: set[str] = set()
        self._lock = asyncio.Lock()

    def open(self, permit_id: str) -> None:
        """Reset closed state for a permit when a new saga starts."""
        self._closed_permits.discard(permit_id)

    def subscribe(self, permit_id: str) -> asyncio.Queue[Optional[TraceEvent]]:
        """Subscribe to live TraceEvents for a specific permit_id."""
        q: asyncio.Queue[Optional[TraceEvent]] = asyncio.Queue()
        if permit_id in self._closed_permits:
            q.put_nowait(None)
            return q
        if permit_id not in self._subscribers:
            self._subscribers[permit_id] = []
        self._subscribers[permit_id].append(q)
        return q

    def unsubscribe(self, permit_id: str, queue: asyncio.Queue[Optional[TraceEvent]]) -> None:
        """Unsubscribe a queue from a permit's live stream."""
        if permit_id in self._subscribers:
            try:
                self._subscribers[permit_id].remove(queue)
            except ValueError:
                pass
            if not self._subscribers[permit_id]:
                del self._subscribers[permit_id]

    def publish(self, permit_id: str, event: TraceEvent) -> None:
        """Publish a TraceEvent to all active subscribers for the permit."""
        self._closed_permits.discard(permit_id)
        queues = list(self._subscribers.get(permit_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def close(self, permit_id: str) -> None:
        """Signal completion to all subscribers for the permit by pushing None."""
        self._closed_permits.add(permit_id)
        queues = self._subscribers.pop(permit_id, [])
        for q in queues:
            try:
                q.put_nowait(None)
            except Exception:
                pass

    def clear(self) -> None:
        """Clear all active subscriptions."""
        for permit_id in list(self._subscribers.keys()):
            self.close(permit_id)
        self._subscribers.clear()
        self._closed_permits.clear()


# Default singleton broadcaster instance
broadcaster = EventBroadcaster()


def get_broadcaster() -> EventBroadcaster:
    """Return the global event broadcaster instance."""
    return broadcaster


# Tracking of active saga events and tasks
permit_events: Dict[str, asyncio.Event] = {}
permit_tasks: Dict[str, asyncio.Task] = {}


def get_permit_event(permit_id: str) -> Optional[asyncio.Event]:
    """Retrieve completion event for a permit saga."""
    return permit_events.get(permit_id)


async def run_permit_saga(
    permit_id: str,
    trace_id: str,
    permit: Permit,
    broadcaster_inst: Optional[EventBroadcaster] = None,
    dispatcher_fn: Optional[Callable[..., Any]] = None,
    consensus_fn: Optional[Callable[..., Any]] = None,
    endpoints: Optional[Dict[str, str]] = None,
    event: Optional[asyncio.Event] = None,
    db_module: Optional[Any] = None,
    db_path: Optional[Union[Path, str]] = None,
    state_sink: Optional[Any] = None,
) -> None:
    """Execute asynchronous saga: department fan-out, consensus, SQLite persistence, and trace event broadcasting."""
    active_broadcaster = broadcaster_inst or broadcaster
    if hasattr(active_broadcaster, "open"):
        active_broadcaster.open(permit_id)

    # Resolve DB module
    if db_module is None:
        try:
            from orchestrator import db as db_module
        except ImportError:
            db_module = None

    def handle_trace_event(ev: TraceEvent) -> None:
        # 1. SQLite append (append-only)
        if db_module is not None and hasattr(db_module, "append_trace_event"):
            try:
                db_module.append_trace_event(ev, db_path=db_path)
            except Exception as exc:
                logger.warning(f"Failed to append trace event {ev.event_id} to DB: {exc}")
        # 2. In-memory sink if provided
        if state_sink is not None and hasattr(state_sink, "record_event"):
            try:
                state_sink.record_event(permit_id, ev)
            except Exception:
                pass
        # 3. Broadcaster publish
        active_broadcaster.publish(permit_id, ev)

    try:
        # Mark state as EVALUATING in DB and state_sink
        if db_module is not None and hasattr(db_module, "update_permit_state"):
            db_module.update_permit_state(permit_id, SagaState.EVALUATING, db_path=db_path)

        if state_sink is not None and hasattr(state_sink, "permits_db") and permit_id in state_sink.permits_db:
            state_sink.permits_db[permit_id].state = SagaState.EVALUATING

        # 1. Dispatch permit evaluation across 4 departments
        department_results: Dict[str, DepartmentResultSummary]
        if dispatcher_fn is not None:
            sig = inspect.signature(dispatcher_fn)
            kwargs = {}
            if "permit" in sig.parameters:
                kwargs["permit"] = permit
            if "trace_id" in sig.parameters:
                kwargs["trace_id"] = trace_id
            if "on_event" in sig.parameters:
                kwargs["on_event"] = handle_trace_event
            if "endpoints" in sig.parameters and endpoints is not None:
                kwargs["endpoints"] = endpoints

            if len(kwargs) == len(sig.parameters):
                disp_res = dispatcher_fn(**kwargs)
            else:
                try:
                    disp_res = dispatcher_fn(permit, trace_id, on_event=handle_trace_event)
                except TypeError:
                    try:
                        disp_res = dispatcher_fn(permit, trace_id)
                    except TypeError:
                        disp_res = dispatcher_fn(permit)

            if inspect.isawaitable(disp_res):
                department_results = await disp_res
            else:
                department_results = disp_res
        else:
            http_client = getattr(state_sink, "http_client", None)
            backoff_delays = getattr(state_sink, "backoff_delays", None)
            timeout_seconds = getattr(state_sink, "timeout_seconds", None)
            effective_endpoints = endpoints or getattr(state_sink, "endpoints", None)

            department_results = await dispatch_permit(
                permit=permit,
                trace_id=trace_id,
                endpoints=effective_endpoints,
                client=http_client,
                backoff_delays=backoff_delays,
                timeout_seconds=timeout_seconds,
                on_event=handle_trace_event,
            )

        # 2. Emit CONSENSUS_STARTED event
        consensus_event = TraceEvent(
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
        )
        if db_module is not None and hasattr(db_module, "append_trace_event"):
            try:
                db_module.append_trace_event(consensus_event, db_path=db_path)
            except Exception as exc:
                logger.warning(f"Failed to append consensus event: {exc}")
        if state_sink is not None and hasattr(state_sink, "record_event"):
            try:
                state_sink.record_event(permit_id, consensus_event)
            except Exception:
                pass
        active_broadcaster.publish(permit_id, consensus_event)

        # 3. Run consensus evaluation
        if consensus_fn is not None:
            final_decision, decision_code, summary, violations = consensus_fn(department_results)
        else:
            final_decision, decision_code, summary, violations = evaluate_consensus(department_results)

        decided_time = datetime.now(timezone.utc)

        # 4. Persist department results in SQLite
        if db_module is not None and hasattr(db_module, "save_department_result"):
            for dept_name, res in department_results.items():
                db_module.save_department_result(
                    permit_id=permit_id,
                    department=dept_name,
                    status=res.status if isinstance(res.status, str) else res.status.value,
                    attempts=res.attempts,
                    latency_ms=res.latency_ms,
                    reasons=res.reasons,
                    error_message=res.error_message,
                    db_path=db_path,
                )

        # 5. Update permit record in SQLite with final_decision, decision_code, decided_at
        if db_module is not None and hasattr(db_module, "update_permit_decision"):
            db_module.update_permit_decision(
                permit_id=permit_id,
                state=SagaState.DECIDED,
                final_decision=final_decision,
                decision_code=decision_code,
                decision_summary=summary,
                decided_at=decided_time,
                db_path=db_path,
            )

        # Update in-memory state sink if provided
        if state_sink is not None and hasattr(state_sink, "permits_db") and permit_id in state_sink.permits_db:
            rec = state_sink.permits_db[permit_id]
            rec.department_results = department_results
            rec.final_decision = final_decision
            rec.decision_code = decision_code
            rec.decision_summary = summary
            rec.decided_at = decided_time
            rec.state = SagaState.DECIDED

        # 6. Emit TraceEventType.PERMIT_DECIDED to SQLite and notify SSE subscribers
        decided_event = TraceEvent(
            event_id=f"evt-{uuid4().hex[:8]}",
            trace_id=trace_id,
            permit_id=permit_id,
            span=SpanId.ORCHESTRATOR,
            parent_span=None,
            type=TraceEventType.PERMIT_DECIDED,
            ts=decided_time,
            detail={
                "state": SagaState.DECIDED.value,
                "final_decision": final_decision.value if hasattr(final_decision, "value") else str(final_decision),
                "decision_code": decision_code.value if hasattr(decision_code, "value") else str(decision_code),
                "decision_summary": summary,
                "violations": violations,
            },
        )
        if db_module is not None and hasattr(db_module, "append_trace_event"):
            try:
                db_module.append_trace_event(decided_event, db_path=db_path)
            except Exception as exc:
                logger.warning(f"Failed to append decided event: {exc}")
        if state_sink is not None and hasattr(state_sink, "record_event"):
            try:
                state_sink.record_event(permit_id, decided_event)
            except Exception:
                pass
        active_broadcaster.publish(permit_id, decided_event)

    except Exception as exc:
        # Fail-safe recovery (Goal #1)
        decided_time = datetime.now(timezone.utc)
        err_summary = f"Saga execution failed: {exc}"
        logger.error(err_summary, exc_info=True)

        if db_module is not None and hasattr(db_module, "update_permit_decision"):
            try:
                db_module.update_permit_decision(
                    permit_id=permit_id,
                    state=SagaState.DECIDED,
                    final_decision=FinalDecision.REJECTED,
                    decision_code=DecisionCode.INCOMPLETE_REVIEWS,
                    decision_summary=err_summary,
                    decided_at=decided_time,
                    db_path=db_path,
                )
            except Exception as dberr:
                logger.error(f"Failed to update fail-safe decision in DB: {dberr}")

        if state_sink is not None and hasattr(state_sink, "permits_db") and permit_id in state_sink.permits_db:
            rec = state_sink.permits_db[permit_id]
            rec.final_decision = FinalDecision.REJECTED
            rec.decision_code = DecisionCode.INCOMPLETE_REVIEWS
            rec.decision_summary = err_summary
            rec.decided_at = decided_time
            rec.state = SagaState.DECIDED

        fail_event = TraceEvent(
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
        )
        if db_module is not None and hasattr(db_module, "append_trace_event"):
            try:
                db_module.append_trace_event(fail_event, db_path=db_path)
            except Exception as dberr:
                logger.error(f"Failed to append fail_event in DB: {dberr}")
        if state_sink is not None and hasattr(state_sink, "record_event"):
            try:
                state_sink.record_event(permit_id, fail_event)
            except Exception:
                pass
        active_broadcaster.publish(permit_id, fail_event)

    finally:
        active_broadcaster.close(permit_id)
        if state_sink is not None and hasattr(state_sink, "close_stream_subscribers"):
            try:
                state_sink.close_stream_subscribers(permit_id)
            except Exception:
                pass
        if event is not None:
            event.set()


router = APIRouter(tags=["submit"])


@router.post(
    "/submit",
    response_model=SubmitPermitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit permit application",
)
async def submit_permit(
    permit: Permit,
    request: Request,
    wait: bool = Query(
        False,
        description="Whether to await saga fan-out and consensus before returning",
    ),
) -> SubmitPermitResponse:
    """
    POST /submit endpoint:
    - Validates Permit JSON (from contract.models.Permit)
    - Persists permit record to SQLite with SagaState.RECEIVED
    - Emits root TraceEvent (TraceEventType.PERMIT_RECEIVED) to SQLite and event broadcaster
    - Spawns asynchronous background saga
    - Returns HTTP 202 Accepted with SubmitPermitResponse
    """
    permit_id = permit.permit_id if permit.permit_id else str(uuid4())
    if permit.permit_id != permit_id:
        permit = permit.model_copy(update={"permit_id": permit_id})
    trace_id = str(uuid4())
    created_at = datetime.now(timezone.utc)

    # Resolve db module and db_path
    db_path = None
    if request is not None and hasattr(request.app.state, "db_path"):
        db_path = request.app.state.db_path

    try:
        from orchestrator import db as db_module
    except ImportError:
        db_module = None

    # 1. Persist permit record to SQLite with SagaState.RECEIVED
    if db_module is not None and hasattr(db_module, "save_permit"):
        db_module.save_permit(
            permit_id=permit_id,
            payload=permit,
            state=SagaState.RECEIVED,
            created_at=created_at,
            trace_id=trace_id,
            db_path=db_path,
        )

    # 2. Emit root TraceEvent (TraceEventType.PERMIT_RECEIVED) to SQLite and event broker
    root_event = TraceEvent(
        event_id=f"evt-{uuid4().hex[:8]}",
        trace_id=trace_id,
        permit_id=permit_id,
        span=SpanId.ORCHESTRATOR,
        parent_span=None,
        type=TraceEventType.PERMIT_RECEIVED,
        ts=created_at,
        detail={
            "project_title": permit.project_title,
            "applicant": permit.applicant.name if permit.applicant else "Unknown",
            "state": SagaState.RECEIVED.value,
        },
    )
    if db_module is not None and hasattr(db_module, "append_trace_event"):
        db_module.append_trace_event(root_event, db_path=db_path)

    # Active broadcaster
    active_broadcaster = broadcaster
    if request is not None and hasattr(request.app.state, "broadcaster"):
        active_broadcaster = request.app.state.broadcaster
    if hasattr(active_broadcaster, "open"):
        active_broadcaster.open(permit_id)

    active_broadcaster.publish(permit_id, root_event)

    # Resolve overrides and state sink
    dispatcher_fn = None
    consensus_fn = None
    state_sink = None
    if request is not None:
        if hasattr(request.app.state, "dispatcher_override") and request.app.state.dispatcher_override is not None:
            dispatcher_fn = request.app.state.dispatcher_override
        if hasattr(request.app.state, "consensus_override") and request.app.state.consensus_override is not None:
            consensus_fn = request.app.state.consensus_override
        if hasattr(request.app.state, "orchestrator") and request.app.state.orchestrator is not None:
            state_sink = request.app.state.orchestrator
            if state_sink.custom_dispatcher is not None:
                dispatcher_fn = state_sink.custom_dispatcher
            if state_sink.custom_consensus is not None:
                consensus_fn = state_sink.custom_consensus
            # Initialize in-memory status record for backward compatibility
            initial_dept_results = {
                dept.value: DepartmentResultSummary(
                    status=DepartmentStatus.PENDING,
                    attempts=0,
                    latency_ms=0.0,
                    reasons=[],
                )
                for dept in DepartmentName
            }
            initial_status = PermitStatusResponse(
                permit_id=permit_id,
                trace_id=trace_id,
                state=SagaState.RECEIVED,
                final_decision=FinalDecision.PENDING,
                decision_code=DecisionCode.PENDING,
                created_at=created_at,
                department_results=initial_dept_results,
            )
            state_sink.permits_db[permit_id] = initial_status
            state_sink.record_event(permit_id, root_event)

    # Completion event
    completion_event = asyncio.Event()
    permit_events[permit_id] = completion_event
    if state_sink is not None and hasattr(state_sink, "permit_events"):
        state_sink.permit_events[permit_id] = completion_event

    saga_coro = run_permit_saga(
        permit_id=permit_id,
        trace_id=trace_id,
        permit=permit,
        broadcaster_inst=active_broadcaster,
        dispatcher_fn=dispatcher_fn,
        consensus_fn=consensus_fn,
        event=completion_event,
        db_module=db_module,
        db_path=db_path,
        state_sink=state_sink,
    )

    if wait:
        try:
            await saga_coro
        finally:
            permit_events.pop(permit_id, None)
    else:
        task = asyncio.create_task(saga_coro)
        if state_sink is not None and hasattr(state_sink, "background_tasks"):
            state_sink.background_tasks.add(task)
            task.add_done_callback(state_sink.background_tasks.discard)
        permit_tasks[permit_id] = task
        task.add_done_callback(lambda _: permit_tasks.pop(permit_id, None))
        task.add_done_callback(lambda _: permit_events.pop(permit_id, None))

    return SubmitPermitResponse(
        permit_id=permit_id,
        trace_id=trace_id,
        state=SagaState.RECEIVED,
        stream_url=f"/permits/{permit_id}/stream",
    )
