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

from fastapi import APIRouter, Header, Query, Request, status

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
        """Clear all active subscriptions and submission tracking."""
        for permit_id in list(self._subscribers.keys()):
            self.close(permit_id)
        self._subscribers.clear()
        self._closed_permits.clear()
        submitted_permits.clear()
        permit_trace_ids.clear()
        permit_events.clear()
        for task in list(permit_tasks.values()):
            if not task.done():
                task.cancel()
        permit_tasks.clear()


# Default singleton broadcaster instance
broadcaster = EventBroadcaster()


def get_broadcaster(app: Optional[Any] = None) -> EventBroadcaster:
    """Return the active event broadcaster instance."""
    if app is not None and hasattr(app, "state") and hasattr(app.state, "broadcaster") and app.state.broadcaster is not None:
        return app.state.broadcaster
    return broadcaster


# Tracking of active saga events, tasks, and submission responses for idempotency
permit_events: Dict[str, asyncio.Event] = {}
permit_tasks: Dict[str, asyncio.Task] = {}
submitted_permits: Dict[str, SubmitPermitResponse] = {}
permit_trace_ids: Dict[str, str] = {}
_submission_lock = asyncio.Lock()


def reset_submit_state() -> None:
    """Clear all active and recorded permit submission tracking."""
    broadcaster.clear()


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
    idempotency_key: Optional[str] = Header(
        None,
        alias="Idempotency-Key",
        description="Optional client idempotency key",
    ),
) -> SubmitPermitResponse:
    """
    POST /submit endpoint:
    - Validates Permit JSON (from contract.models.Permit)
    - Supports idempotency: identical permit_id or Idempotency-Key returns existing permit acknowledgment
    - Persists initial permit record to SQLite with SagaState.RECEIVED
    - Emits root TraceEvent (TraceEventType.PERMIT_RECEIVED) to SQLite and event broadcaster
    - Spawns asynchronous background saga (or awaits fan-out and consensus if wait=True)
    - Returns HTTP 202 Accepted with SubmitPermitResponse
    """
    raw_header_key = None
    if request is not None and hasattr(request, "headers"):
        raw_header_key = request.headers.get("x-idempotency-key") or request.headers.get("idempotency-key")
    effective_idemp_key = idempotency_key or raw_header_key

    has_client_id = bool(permit.permit_id or effective_idemp_key)
    if not permit.permit_id and effective_idemp_key:
        permit_id = str(effective_idemp_key)
        permit = permit.model_copy(update={"permit_id": permit_id})
    elif permit.permit_id:
        permit_id = str(permit.permit_id)
    else:
        permit_id = str(uuid4())
        permit = permit.model_copy(update={"permit_id": permit_id})

    # Resolve db module and db_path
    db_path = None
    if request is not None and hasattr(request.app.state, "db_path"):
        db_path = request.app.state.db_path

    try:
        from orchestrator import db as db_module
    except ImportError:
        db_module = None

    state_sink = None
    if request is not None and hasattr(request.app.state, "orchestrator"):
        state_sink = request.app.state.orchestrator

    async with _submission_lock:
        is_known = False
        existing_trace_id: Optional[str] = None
        existing_state: SagaState = SagaState.RECEIVED

        if has_client_id:
            # 1. Check in-memory submitted permits
            if permit_id in submitted_permits:
                is_known = True
                existing_trace_id = submitted_permits[permit_id].trace_id

            # 2. Check in SQLite DB
            if not is_known and db_module is not None and hasattr(db_module, "get_permit"):
                try:
                    db_rec = db_module.get_permit(permit_id, db_path=db_path)
                    if db_rec is not None:
                        is_known = True
                        existing_trace_id = db_rec.trace_id
                        existing_state = db_rec.state
                except Exception:
                    pass

            # 3. Check in state_sink
            if not is_known and state_sink is not None and hasattr(state_sink, "permits_db"):
                mem_rec = state_sink.permits_db.get(permit_id)
                if mem_rec is not None:
                    is_known = True
                    existing_trace_id = getattr(mem_rec, "trace_id", None)
                    existing_state = getattr(mem_rec, "state", SagaState.RECEIVED)

            # 4. Check in-flight tasks / events
            if not is_known and (permit_id in permit_events or permit_id in permit_tasks):
                is_known = True
                existing_trace_id = permit_trace_ids.get(permit_id)

        if is_known:
            if not existing_trace_id:
                existing_trace_id = permit_trace_ids.get(permit_id, str(uuid4()))
            completion_ev = permit_events.get(permit_id)
            if completion_ev is None and state_sink is not None and hasattr(state_sink, "permit_events"):
                completion_ev = state_sink.permit_events.get(permit_id)
        else:
            trace_id = str(uuid4())
            created_at = datetime.now(timezone.utc)
            permit_trace_ids[permit_id] = trace_id

            submit_resp = SubmitPermitResponse(
                permit_id=permit_id,
                trace_id=trace_id,
                state=SagaState.RECEIVED,
                stream_url=f"/permits/{permit_id}/stream",
            )
            submitted_permits[permit_id] = submit_resp

            # Completion event
            completion_event = asyncio.Event()
            permit_events[permit_id] = completion_event
            if state_sink is not None and hasattr(state_sink, "permit_events"):
                state_sink.permit_events[permit_id] = completion_event

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
            if request is not None:
                if hasattr(request.app.state, "dispatcher_override") and request.app.state.dispatcher_override is not None:
                    dispatcher_fn = request.app.state.dispatcher_override
                if hasattr(request.app.state, "consensus_override") and request.app.state.consensus_override is not None:
                    consensus_fn = request.app.state.consensus_override
                if state_sink is not None:
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

            if not wait:
                task = asyncio.create_task(saga_coro)
                if state_sink is not None and hasattr(state_sink, "background_tasks"):
                    state_sink.background_tasks.add(task)
                    task.add_done_callback(state_sink.background_tasks.discard)
                permit_tasks[permit_id] = task
                task.add_done_callback(lambda _: permit_tasks.pop(permit_id, None))
                task.add_done_callback(lambda _: permit_events.pop(permit_id, None))

    if is_known:
        if wait and completion_ev is not None and existing_state != SagaState.DECIDED:
            await completion_ev.wait()
            if db_module is not None and hasattr(db_module, "get_permit"):
                try:
                    updated = db_module.get_permit(permit_id, db_path=db_path)
                    if updated is not None and updated.trace_id:
                        existing_trace_id = updated.trace_id
                except Exception:
                    pass
        return SubmitPermitResponse(
            permit_id=permit_id,
            trace_id=existing_trace_id,
            state=SagaState.RECEIVED,
            stream_url=f"/permits/{permit_id}/stream",
        )

    if wait:
        try:
            await saga_coro
        finally:
            permit_events.pop(permit_id, None)

    return submit_resp
