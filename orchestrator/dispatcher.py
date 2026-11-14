"""Parallel fan-out dispatcher for municipal department reviews.

Dispatches permit evaluation requests to four municipal departments
('zoning', 'fire', 'water', 'structural') concurrently using asyncio and httpx.
Enforces a 2.0 second per-call timeout, 3 max attempts with exponential/staged backoff
(200ms, 500ms), and returns DepartmentResultSummary per department.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Union
from uuid import uuid4

import httpx

from contract.models import (
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    Permit,
    SpanId,
    TraceEvent,
    TraceEventType,
)

# Constants per specification
TIMEOUT_SECONDS: float = 2.0
MAX_ATTEMPTS: int = 3
DEFAULT_BACKOFF_DELAYS: List[float] = [0.2, 0.5]

ALL_DEPARTMENTS: List[str] = [
    DepartmentName.ZONING.value,
    DepartmentName.FIRE.value,
    DepartmentName.WATER.value,
    DepartmentName.STRUCTURAL.value,
]

DEFAULT_DEPARTMENT_ENDPOINTS: Dict[str, str] = {
    "zoning": os.environ.get("ZONING_URL", "http://zoning:8001"),
    "fire": os.environ.get("FIRE_URL", "http://fire:8002"),
    "water": os.environ.get("WATER_URL", "http://water:8003"),
    "structural": os.environ.get("STRUCTURAL_URL", "http://structural:8004"),
}

SPAN_MAP: Dict[str, SpanId] = {
    "zoning": SpanId.DEPT_ZONING,
    "fire": SpanId.DEPT_FIRE,
    "water": SpanId.DEPT_WATER,
    "structural": SpanId.DEPT_STRUCTURAL,
}


def resolve_endpoint_url(base_or_full_url: str) -> str:
    """Ensure URL points to the /evaluate endpoint."""
    url = base_or_full_url.rstrip("/")
    if url.endswith("/evaluate"):
        return url
    return f"{url}/evaluate"


def get_default_endpoints(overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return department endpoint mapping with environment variable fallbacks and optional overrides."""
    endpoints = {
        "zoning": os.environ.get("ZONING_URL", "http://zoning:8001"),
        "fire": os.environ.get("FIRE_URL", "http://fire:8002"),
        "water": os.environ.get("WATER_URL", "http://water:8003"),
        "structural": os.environ.get("STRUCTURAL_URL", "http://structural:8004"),
    }
    if overrides:
        endpoints.update(overrides)
    return endpoints


async def _emit_trace_event(
    callback: Optional[Callable[[TraceEvent], Any]],
    event_id: str,
    trace_id: str,
    permit_id: str,
    span: SpanId,
    event_type: TraceEventType,
    detail: Dict[str, Any],
    parent_span: Optional[str] = "orchestrator",
) -> None:
    """Helper to emit TraceEvent if a callback is provided."""
    if callback is None:
        return
    event = TraceEvent(
        event_id=event_id,
        trace_id=trace_id,
        permit_id=permit_id,
        span=span,
        parent_span=parent_span,
        type=event_type,
        ts=datetime.now(timezone.utc),
        detail=detail,
    )
    try:
        res = callback(event)
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass


async def dispatch_department(
    dept_name: str,
    endpoint_url: str,
    permit: Permit,
    permit_id: str,
    trace_id: str,
    client: httpx.AsyncClient,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_delays: Optional[Sequence[float]] = None,
    on_event: Optional[Callable[[TraceEvent], Any]] = None,
) -> DepartmentResultSummary:
    """Dispatch evaluation request to a single department with retry and backoff.

    - Per-call timeout: timeout_seconds (default 2.0s)
    - Max attempts: max_attempts (default 3)
    - Backoff on timeout or HTTP 5xx:
      Attempt 1: immediate
      Attempt 2 (retry 1): after backoff_delays[0] (0.2s)
      Attempt 3 (retry 2): after backoff_delays[1] (0.5s)
    - If all attempts exhausted:
      returns DepartmentResultSummary with DepartmentStatus.TIMEOUT (if timed out)
      or DepartmentStatus.ERROR (if 5xx).
    """
    delays = backoff_delays if backoff_delays is not None else DEFAULT_BACKOFF_DELAYS
    url = resolve_endpoint_url(endpoint_url)
    span = SPAN_MAP.get(dept_name, SpanId.ORCHESTRATOR)
    started = time.perf_counter()

    await _emit_trace_event(
        on_event,
        event_id=f"evt-{uuid4().hex[:8]}",
        trace_id=trace_id,
        permit_id=permit_id,
        span=span,
        event_type=TraceEventType.SPAN_STARTED,
        detail={"department": dept_name},
    )

    last_status = DepartmentStatus.ERROR
    last_error_message: Optional[str] = None
    last_reasons: List[EvaluationReason] = []
    attempts_made = 0

    try:
        dept_enum = DepartmentName(dept_name)
    except ValueError:
        dept_enum = DepartmentName.ZONING

    for attempt in range(1, max_attempts + 1):
        attempts_made = attempt

        await _emit_trace_event(
            on_event,
            event_id=f"evt-{uuid4().hex[:8]}",
            trace_id=trace_id,
            permit_id=permit_id,
            span=span,
            event_type=TraceEventType.CALL_DISPATCHED,
            detail={"attempt": attempt, "endpoint": url},
        )

        req_payload = DepartmentEvaluationRequest(
            permit_id=permit_id,
            trace_id=trace_id,
            department=dept_enum,
            attempt=attempt,
            permit=permit,
        ).model_dump(mode="json")

        call_started = time.perf_counter()
        is_timeout = False
        is_5xx = False
        response: Optional[httpx.Response] = None

        try:
            response = await asyncio.wait_for(
                client.post(url, json=req_payload, timeout=timeout_seconds),
                timeout=timeout_seconds,
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            is_timeout = True
            last_status = DepartmentStatus.TIMEOUT
            last_error_message = f"ClientTimeout after {int(timeout_seconds * 1000)}ms"
            await _emit_trace_event(
                on_event,
                event_id=f"evt-{uuid4().hex[:8]}",
                trace_id=trace_id,
                permit_id=permit_id,
                span=span,
                event_type=TraceEventType.CALL_TIMED_OUT,
                detail={
                    "attempt": attempt,
                    "timeout_seconds": timeout_seconds,
                    "error": last_error_message,
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                is_5xx = True
                last_status = DepartmentStatus.ERROR
                last_error_message = f"Department returned HTTP {exc.response.status_code}"
            else:
                # 4xx client errors are non-retryable
                elapsed_ms = (time.perf_counter() - started) * 1000
                await _emit_trace_event(
                    on_event,
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=span,
                    event_type=TraceEventType.CALL_FAILED,
                    detail={
                        "attempts_total": attempt,
                        "final_status": DepartmentStatus.ERROR.value,
                        "error": f"Department returned HTTP {exc.response.status_code}",
                    },
                )
                await _emit_trace_event(
                    on_event,
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=span,
                    event_type=TraceEventType.SPAN_COMPLETED,
                    detail={"status": DepartmentStatus.ERROR.value, "duration_ms": elapsed_ms},
                )
                return DepartmentResultSummary(
                    status=DepartmentStatus.ERROR,
                    attempts=attempt,
                    latency_ms=elapsed_ms,
                    reasons=[],
                    error_message=f"Department returned HTTP {exc.response.status_code}",
                )
        except httpx.RequestError as exc:
            # Network or connection errors (e.g. service down/offline)
            is_5xx = True
            last_status = DepartmentStatus.ERROR
            last_error_message = f"Department connection error: {exc}"
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            error_msg = f"Unexpected error: {exc}"
            await _emit_trace_event(
                on_event,
                event_id=f"evt-{uuid4().hex[:8]}",
                trace_id=trace_id,
                permit_id=permit_id,
                span=span,
                event_type=TraceEventType.CALL_FAILED,
                detail={
                    "attempts_total": attempt,
                    "final_status": DepartmentStatus.ERROR.value,
                    "error": error_msg,
                },
            )
            await _emit_trace_event(
                on_event,
                event_id=f"evt-{uuid4().hex[:8]}",
                trace_id=trace_id,
                permit_id=permit_id,
                span=span,
                event_type=TraceEventType.SPAN_COMPLETED,
                detail={"status": DepartmentStatus.ERROR.value, "duration_ms": elapsed_ms},
            )
            return DepartmentResultSummary(
                status=DepartmentStatus.ERROR,
                attempts=attempt,
                latency_ms=elapsed_ms,
                reasons=[],
                error_message=error_msg,
            )

        # Check response if received
        if response is not None:
            if response.status_code >= 500:
                is_5xx = True
                last_status = DepartmentStatus.ERROR
                last_error_message = f"Department returned HTTP {response.status_code}"
            elif 400 <= response.status_code < 500:
                # 4xx client errors are non-retryable
                elapsed_ms = (time.perf_counter() - started) * 1000
                await _emit_trace_event(
                    on_event,
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=span,
                    event_type=TraceEventType.CALL_FAILED,
                    detail={
                        "attempts_total": attempt,
                        "final_status": DepartmentStatus.ERROR.value,
                        "error": f"Department returned HTTP {response.status_code}",
                    },
                )
                await _emit_trace_event(
                    on_event,
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=span,
                    event_type=TraceEventType.SPAN_COMPLETED,
                    detail={"status": DepartmentStatus.ERROR.value, "duration_ms": elapsed_ms},
                )
                return DepartmentResultSummary(
                    status=DepartmentStatus.ERROR,
                    attempts=attempt,
                    latency_ms=elapsed_ms,
                    reasons=[],
                    error_message=f"Department returned HTTP {response.status_code}",
                )
            elif response.status_code == 200:
                try:
                    data = response.json()
                    eval_resp = DepartmentEvaluationResponse.model_validate(data)
                    if eval_resp.department != dept_enum or eval_resp.permit_id != permit_id:
                        raise ValueError(
                            f"Department response identity mismatch: expected ({dept_enum.value}, {permit_id}), "
                            f"got ({eval_resp.department.value}, {eval_resp.permit_id})"
                        )
                    if eval_resp.status in (DepartmentStatus.APPROVED, DepartmentStatus.REJECTED):
                        elapsed_ms = (time.perf_counter() - started) * 1000
                        call_duration_ms = (time.perf_counter() - call_started) * 1000
                        await _emit_trace_event(
                            on_event,
                            event_id=f"evt-{uuid4().hex[:8]}",
                            trace_id=trace_id,
                            permit_id=permit_id,
                            span=span,
                            event_type=TraceEventType.CALL_SUCCEEDED,
                            detail={
                                "status": eval_resp.status.value,
                                "attempt": attempt,
                                "latency_ms": round(call_duration_ms, 2),
                            },
                        )
                        await _emit_trace_event(
                            on_event,
                            event_id=f"evt-{uuid4().hex[:8]}",
                            trace_id=trace_id,
                            permit_id=permit_id,
                            span=span,
                            event_type=TraceEventType.SPAN_COMPLETED,
                            detail={
                                "status": eval_resp.status.value,
                                "duration_ms": round(elapsed_ms, 2),
                            },
                        )
                        return DepartmentResultSummary(
                            status=eval_resp.status,
                            attempts=attempt,
                            latency_ms=elapsed_ms,
                            reasons=eval_resp.reasons,
                            error_message=None,
                        )
                    else:
                        elapsed_ms = (time.perf_counter() - started) * 1000
                        error_msg = f"Department returned nonterminal status: {eval_resp.status}"
                        await _emit_trace_event(
                            on_event,
                            event_id=f"evt-{uuid4().hex[:8]}",
                            trace_id=trace_id,
                            permit_id=permit_id,
                            span=span,
                            event_type=TraceEventType.CALL_FAILED,
                            detail={
                                "attempts_total": attempt,
                                "final_status": DepartmentStatus.ERROR.value,
                                "error": error_msg,
                            },
                        )
                        await _emit_trace_event(
                            on_event,
                            event_id=f"evt-{uuid4().hex[:8]}",
                            trace_id=trace_id,
                            permit_id=permit_id,
                            span=span,
                            event_type=TraceEventType.SPAN_COMPLETED,
                            detail={"status": DepartmentStatus.ERROR.value, "duration_ms": elapsed_ms},
                        )
                        return DepartmentResultSummary(
                            status=DepartmentStatus.ERROR,
                            attempts=attempt,
                            latency_ms=elapsed_ms,
                            reasons=[],
                            error_message=error_msg,
                        )
                except Exception as exc:
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    error_msg = f"Invalid response schema: {exc}"
                    await _emit_trace_event(
                        on_event,
                        event_id=f"evt-{uuid4().hex[:8]}",
                        trace_id=trace_id,
                        permit_id=permit_id,
                        span=span,
                        event_type=TraceEventType.CALL_FAILED,
                        detail={
                            "attempts_total": attempt,
                            "final_status": DepartmentStatus.ERROR.value,
                            "error": error_msg,
                        },
                    )
                    await _emit_trace_event(
                        on_event,
                        event_id=f"evt-{uuid4().hex[:8]}",
                        trace_id=trace_id,
                        permit_id=permit_id,
                        span=span,
                        event_type=TraceEventType.SPAN_COMPLETED,
                        detail={"status": DepartmentStatus.ERROR.value, "duration_ms": elapsed_ms},
                    )
                    return DepartmentResultSummary(
                        status=DepartmentStatus.ERROR,
                        attempts=attempt,
                        latency_ms=elapsed_ms,
                        reasons=[],
                        error_message=error_msg,
                    )

        # Retry logic for timeout or 5xx
        if is_timeout or is_5xx:
            if attempt < max_attempts:
                delay_index = attempt - 1
                backoff_s = delays[delay_index] if delay_index < len(delays) else delays[-1]
                await _emit_trace_event(
                    on_event,
                    event_id=f"evt-{uuid4().hex[:8]}",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=span,
                    event_type=TraceEventType.CALL_RETRYING,
                    detail={
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "backoff_ms": int(backoff_s * 1000),
                    },
                )
                await asyncio.sleep(backoff_s)
                continue
            else:
                # All attempts exhausted
                break

    # Attempts exhausted
    elapsed_ms = (time.perf_counter() - started) * 1000
    final_error = last_error_message or (
        "ClientTimeout after 2000ms"
        if last_status == DepartmentStatus.TIMEOUT
        else "Retries exhausted"
    )

    await _emit_trace_event(
        on_event,
        event_id=f"evt-{uuid4().hex[:8]}",
        trace_id=trace_id,
        permit_id=permit_id,
        span=span,
        event_type=TraceEventType.CALL_FAILED,
        detail={
            "attempts_total": attempts_made,
            "final_status": last_status.value,
            "error": final_error,
        },
    )
    await _emit_trace_event(
        on_event,
        event_id=f"evt-{uuid4().hex[:8]}",
        trace_id=trace_id,
        permit_id=permit_id,
        span=span,
        event_type=TraceEventType.SPAN_COMPLETED,
        detail={"status": last_status.value, "duration_ms": round(elapsed_ms, 2)},
    )

    return DepartmentResultSummary(
        status=last_status,
        attempts=attempts_made,
        latency_ms=elapsed_ms,
        reasons=last_reasons,
        error_message=final_error,
    )


async def dispatch_permit(
    permit: Union[Permit, dict],
    trace_id: Optional[str] = None,
    endpoints: Optional[Dict[str, str]] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout_seconds: float = TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_delays: Optional[Sequence[float]] = None,
    departments: Optional[List[str]] = None,
    on_event: Optional[Callable[[TraceEvent], Any]] = None,
) -> Dict[str, DepartmentResultSummary]:
    """Execute parallel fan-out to all four departments and collect results.

    Parameters:
    - permit: Permit model instance or dictionary.
    - trace_id: Optional trace ID; generated if omitted.
    - endpoints: Optional mapping of department name -> URL. Defaults to env/standard URLs.
    - client: Optional httpx.AsyncClient. Created and closed automatically if omitted.
    - timeout_seconds: Per-call timeout (default TIMEOUT_SECONDS = 2.0s).
    - max_attempts: Max attempts with retries (default 3).
    - backoff_delays: Retry backoffs (default [0.2, 0.5]).
    - departments: Target departments (default ['zoning', 'fire', 'water', 'structural']).
    - on_event: Optional callback to receive TraceEvent objects during execution.

    Returns:
    - Dict[str, DepartmentResultSummary] with latency_ms, status, attempts, reasons, error_message.
    """
    if isinstance(permit, dict):
        permit_obj = Permit.model_validate(permit)
    else:
        permit_obj = permit

    permit_id = permit_obj.permit_id or str(uuid4())
    if permit_obj.permit_id != permit_id:
        permit_obj = permit_obj.model_copy(update={"permit_id": permit_id})

    if trace_id is None:
        trace_id = str(uuid4())

    configured_endpoints = get_default_endpoints(endpoints)
    target_depts = departments or ALL_DEPARTMENTS

    async def _run_with_client(http_client: httpx.AsyncClient) -> Dict[str, DepartmentResultSummary]:
        tasks = [
            dispatch_department(
                dept_name=dept,
                endpoint_url=configured_endpoints.get(dept, f"http://{dept}:8000"),
                permit=permit_obj,
                permit_id=permit_id,
                trace_id=trace_id,
                client=http_client,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                backoff_delays=backoff_delays,
                on_event=on_event,
            )
            for dept in target_depts
        ]
        results = await asyncio.gather(*tasks)
        return dict(zip(target_depts, results))

    if client is not None:
        return await _run_with_client(client)
    else:
        async with httpx.AsyncClient(timeout=timeout_seconds) as new_client:
            return await _run_with_client(new_client)


# Convenient aliases
dispatch = dispatch_permit


class Dispatcher:
    """Object-oriented interface for the permit dispatcher."""

    def __init__(
        self,
        endpoints: Optional[Dict[str, str]] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout_seconds: float = TIMEOUT_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_delays: Optional[Sequence[float]] = None,
    ):
        self.endpoints = endpoints
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_delays = backoff_delays

    async def dispatch(
        self,
        permit: Union[Permit, dict],
        trace_id: Optional[str] = None,
        endpoints: Optional[Dict[str, str]] = None,
        on_event: Optional[Callable[[TraceEvent], Any]] = None,
    ) -> Dict[str, DepartmentResultSummary]:
        return await dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            endpoints=endpoints or self.endpoints,
            client=self.client,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            backoff_delays=self.backoff_delays,
            on_event=on_event,
        )

    async def dispatch_permit(
        self,
        permit: Union[Permit, dict],
        trace_id: Optional[str] = None,
        endpoints: Optional[Dict[str, str]] = None,
        on_event: Optional[Callable[[TraceEvent], Any]] = None,
    ) -> Dict[str, DepartmentResultSummary]:
        return await self.dispatch(
            permit=permit,
            trace_id=trace_id,
            endpoints=endpoints,
            on_event=on_event,
        )
