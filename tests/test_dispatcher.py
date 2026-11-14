"""Unit tests for the orchestrator parallel fan-out dispatcher."""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple
from unittest.mock import MagicMock

import httpx
import pytest

from contract.models import (
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    Permit,
    TraceEvent,
    TraceEventType,
)
from orchestrator.dispatcher import (
    ALL_DEPARTMENTS,
    DEFAULT_BACKOFF_DELAYS,
    DEFAULT_DEPARTMENT_ENDPOINTS,
    MAX_ATTEMPTS,
    TIMEOUT_SECONDS,
    Dispatcher,
    dispatch_department,
    dispatch_permit,
    get_default_endpoints,
    resolve_endpoint_url,
)


def load_sample_permit() -> Permit:
    """Load a sample permit model from contract fixtures."""
    path = Path("contract/fixtures/permit_approved.json")
    data = json.loads(path.read_text())
    return Permit.model_validate(data)


def make_eval_response(
    permit_id: str,
    department: str,
    status: DepartmentStatus = DepartmentStatus.APPROVED,
    rule_id: str = "RULE-001",
    message: str = "Passed check",
) -> dict:
    """Construct a mock DepartmentEvaluationResponse payload."""
    return {
        "permit_id": permit_id,
        "department": department,
        "status": status.value,
        "execution_time_ms": 15.0,
        "reasons": [
            {
                "rule_id": rule_id,
                "rule_name": f"{department.title()} Check",
                "passed": status == DepartmentStatus.APPROVED,
                "severity": "VIOLATION",
                "message": message,
            }
        ],
    }


def test_constants_and_defaults():
    """Verify specification constants and configuration defaults."""
    assert TIMEOUT_SECONDS == 2.0
    assert MAX_ATTEMPTS == 3
    assert DEFAULT_BACKOFF_DELAYS == [0.2, 0.5]
    assert sorted(ALL_DEPARTMENTS) == ["fire", "structural", "water", "zoning"]
    assert resolve_endpoint_url("http://localhost:8001") == "http://localhost:8001/evaluate"
    assert resolve_endpoint_url("http://localhost:8001/evaluate") == "http://localhost:8001/evaluate"
    assert resolve_endpoint_url("http://localhost:8001/") == "http://localhost:8001/evaluate"

    defaults = get_default_endpoints({"zoning": "http://override:9000"})
    assert defaults["zoning"] == "http://override:9000"
    assert "fire" in defaults


def test_parallel_fanout_all_success():
    """1. Parallel fan-out when all 4 departments respond successfully.

    Verifies:
    - All 4 departments ('zoning', 'fire', 'water', 'structural') are called concurrently.
    - Each department returns DepartmentStatus.APPROVED with attempts=1.
    - Reasons and latency are populated, error_message is None.
    - Request payload matches DepartmentEvaluationRequest contract.
    """
    async def _test():
        permit = load_sample_permit()
        calls: List[Tuple[str, int, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            dept = body["department"]
            attempt = body["attempt"]
            permit_id = body["permit_id"]
            calls.append((dept, attempt, permit_id))
            return httpx.Response(200, json=make_eval_response(permit_id, dept, DepartmentStatus.APPROVED))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await dispatch_permit(
                permit=permit,
                trace_id="test-trace-123",
                client=client,
            )

        assert set(results.keys()) == {"zoning", "fire", "water", "structural"}
        for dept_name in ALL_DEPARTMENTS:
            summary = results[dept_name]
            assert isinstance(summary, DepartmentResultSummary)
            assert summary.status == DepartmentStatus.APPROVED
            assert summary.attempts == 1
            assert summary.error_message is None
            assert len(summary.reasons) == 1
            assert summary.reasons[0].rule_id == "RULE-001"
            assert summary.latency_ms > 0

        # Verify all 4 departments were called with attempt=1
        assert len(calls) == 4
        called_depts = {dept for dept, attempt, _ in calls}
        assert called_depts == {"zoning", "fire", "water", "structural"}
        for dept, attempt, p_id in calls:
            assert attempt == 1
            assert p_id == permit.permit_id

    asyncio.run(_test())


def test_single_department_timeout_retries_and_no_blocking():
    """2. Single department timing out at 2s, exercising retries (200ms, 500ms backoff)

    and returning TIMEOUT after 3 attempts without blocking other departments.

    Verifies:
    - Fire department times out on attempt 1, retry 1 (200ms backoff), and retry 2 (500ms backoff).
    - Fire returns DepartmentStatus.TIMEOUT after 3 attempts.
    - Zoning, Water, and Structural succeed immediately (< 50ms) and are NOT blocked
      by Fire's timeout and backoff delays.
    - Backoff delays between fire attempts match 200ms and 500ms.
    """
    async def _test():
        permit = load_sample_permit()
        fire_call_times: List[float] = []
        other_completion_times: Dict[str, float] = {}
        test_start = time.perf_counter()

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            dept = body["department"]
            now = time.perf_counter()
            if dept == "fire":
                fire_call_times.append(now)
                # Simulate timeout on the HTTP client call
                raise httpx.ReadTimeout("Fire department timed out after 2.0s", request=request)
            else:
                other_completion_times[dept] = now - test_start
                return httpx.Response(200, json=make_eval_response(body["permit_id"], dept, DepartmentStatus.APPROVED))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await dispatch_permit(
                permit=permit,
                trace_id="test-trace-timeout",
                client=client,
            )

        # 1. Fire department assertions
        assert results["fire"].status == DepartmentStatus.TIMEOUT
        assert results["fire"].attempts == 3
        assert results["fire"].error_message is not None
        assert "Timeout" in results["fire"].error_message or "timed out" in results["fire"].error_message.lower()
        assert results["fire"].reasons == []

        # 2. Other 3 departments completed successfully
        for dept in ["zoning", "water", "structural"]:
            assert results[dept].status == DepartmentStatus.APPROVED
            assert results[dept].attempts == 1
            assert results[dept].error_message is None
            # Non-blocking check: other departments completed in the initial burst (< 200ms)
            assert other_completion_times[dept] < 0.20

        # 3. Verify Fire retries and backoff intervals:
        # Call 1 -> backoff ~200ms -> Call 2 -> backoff ~500ms -> Call 3
        assert len(fire_call_times) == 3
        interval_1 = fire_call_times[1] - fire_call_times[0]
        interval_2 = fire_call_times[2] - fire_call_times[1]

        # Allow small tolerance for timer jitter (e.g. 0.18s <= interval_1 <= 0.35s)
        assert interval_1 >= 0.18, f"Expected ~0.20s backoff for retry 1, got {interval_1:.3f}s"
        assert interval_2 >= 0.45, f"Expected ~0.50s backoff for retry 2, got {interval_2:.3f}s"

    asyncio.run(_test())


def test_per_call_timeout_at_2_seconds():
    """Verify that a hanging call triggers per-call timeout at exactly 2.0s."""
    async def _test():
        permit = load_sample_permit()

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body["department"] == "fire":
                # Sleep longer than the 2.0s timeout
                await asyncio.sleep(2.5)
            return httpx.Response(200, json=make_eval_response(body["permit_id"], body["department"]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as client:
            # Test a single attempt with fast backoffs to verify the 2.0s timeout is enforced
            summary = await dispatch_department(
                dept_name="fire",
                endpoint_url="http://fire:8002/evaluate",
                permit=permit,
                permit_id=permit.permit_id or "p1",
                trace_id="t1",
                client=client,
                timeout_seconds=2.0,
                max_attempts=1,
            )

        assert summary.status == DepartmentStatus.TIMEOUT
        assert summary.attempts == 1
        # Latency should be approximately 2.0 seconds
        assert 1900 <= summary.latency_ms <= 2400

    asyncio.run(_test())


def test_5xx_error_retry_and_fallback():
    """3. 5xx error retry and fallback.

    Verifies:
    - Department returning 500, 502, 503 across 3 attempts exercises backoff (200ms, 500ms).
    - After 3 attempts fail with 5xx, department status is marked DepartmentStatus.ERROR.
    - error_message records the 5xx HTTP status.
    - Other healthy departments complete successfully.
    """
    async def _test():
        permit = load_sample_permit()
        call_times: List[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            dept = body["department"]
            if dept == "structural":
                call_times.append(time.perf_counter())
                codes = [500, 502, 503]
                status_code = codes[len(call_times) - 1] if len(call_times) <= len(codes) else 500
                return httpx.Response(status_code, json={"error": f"Server failure {status_code}"})
            return httpx.Response(200, json=make_eval_response(body["permit_id"], dept))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await dispatch_permit(
                permit=permit,
                trace_id="test-trace-5xx",
                client=client,
            )

        # Structural department failed with ERROR
        assert results["structural"].status == DepartmentStatus.ERROR
        assert results["structural"].attempts == 3
        assert "503" in results["structural"].error_message or "500" in results["structural"].error_message
        assert len(call_times) == 3

        # Verify backoff was exercised
        assert call_times[1] - call_times[0] >= 0.18
        assert call_times[2] - call_times[1] >= 0.45

        # Other 3 departments are APPROVED
        for dept in ["zoning", "fire", "water"]:
            assert results[dept].status == DepartmentStatus.APPROVED
            assert results[dept].attempts == 1

    asyncio.run(_test())


def test_5xx_transient_recovery():
    """Verify successful recovery when department fails on attempt 1 with 500, then succeeds on retry 1."""
    async def _test():
        permit = load_sample_permit()
        water_attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal water_attempts
            body = json.loads(request.content)
            dept = body["department"]
            if dept == "water":
                water_attempts += 1
                if water_attempts == 1:
                    return httpx.Response(500, json={"detail": "Temporary glitch"})
                return httpx.Response(200, json=make_eval_response(body["permit_id"], "water"))
            return httpx.Response(200, json=make_eval_response(body["permit_id"], dept))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await dispatch_permit(permit=permit, client=client)

        assert results["water"].status == DepartmentStatus.APPROVED
        assert results["water"].attempts == 2
        assert results["water"].error_message is None
        assert water_attempts == 2

    asyncio.run(_test())


def test_timeout_transient_recovery():
    """Verify successful recovery when department times out on attempt 1, then succeeds on retry 1."""
    async def _test():
        permit = load_sample_permit()
        zoning_attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal zoning_attempts
            body = json.loads(request.content)
            dept = body["department"]
            if dept == "zoning":
                zoning_attempts += 1
                if zoning_attempts == 1:
                    raise httpx.ReadTimeout("Transient timeout", request=request)
                return httpx.Response(200, json=make_eval_response(body["permit_id"], "zoning"))
            return httpx.Response(200, json=make_eval_response(body["permit_id"], dept))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await dispatch_permit(permit=permit, client=client)

        assert results["zoning"].status == DepartmentStatus.APPROVED
        assert results["zoning"].attempts == 2
        assert results["zoning"].error_message is None
        assert zoning_attempts == 2

    asyncio.run(_test())


def test_4xx_client_error_fails_immediately_without_retry():
    """Verify that a 4xx HTTP error (e.g. 422 Unprocessable Entity) is marked ERROR immediately."""
    async def _test():
        permit = load_sample_permit()
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            body = json.loads(request.content)
            if body["department"] == "zoning":
                calls += 1
                return httpx.Response(422, json={"detail": "Invalid permit payload"})
            return httpx.Response(200, json=make_eval_response(body["permit_id"], body["department"]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await dispatch_permit(permit=permit, client=client)

        assert results["zoning"].status == DepartmentStatus.ERROR
        assert results["zoning"].attempts == 1  # No retry on 4xx
        assert "422" in results["zoning"].error_message
        assert calls == 1

    asyncio.run(_test())


def test_department_rejected_status_is_preserved():
    """Verify that when a department returns DepartmentStatus.REJECTED, it is preserved with reasons."""
    async def _test():
        permit = load_sample_permit()

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            dept = body["department"]
            if dept == "fire":
                return httpx.Response(
                    200,
                    json=make_eval_response(
                        body["permit_id"],
                        dept,
                        DepartmentStatus.REJECTED,
                        rule_id="FIRE-002",
                        message="Missing sprinkler system",
                    ),
                )
            return httpx.Response(200, json=make_eval_response(body["permit_id"], dept))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await dispatch_permit(permit=permit, client=client)

        assert results["fire"].status == DepartmentStatus.REJECTED
        assert results["fire"].attempts == 1
        assert len(results["fire"].reasons) == 1
        assert results["fire"].reasons[0].rule_id == "FIRE-002"
        assert results["fire"].reasons[0].passed is False

    asyncio.run(_test())


def test_configurable_endpoints_and_overrides():
    """Verify that custom endpoints can be passed to override default URLs."""
    async def _test():
        permit = load_sample_permit()
        received_urls: List[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received_urls.append(str(request.url))
            body = json.loads(request.content)
            return httpx.Response(200, json=make_eval_response(body["permit_id"], body["department"]))

        custom_endpoints = {
            "zoning": "http://custom-zoning.internal:9001",
            "fire": "http://custom-fire.internal:9002/evaluate",
            "water": "http://custom-water.internal:9003",
            "structural": "http://custom-structural.internal:9004/evaluate",
        }

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await dispatch_permit(
                permit=permit,
                endpoints=custom_endpoints,
                client=client,
            )

        assert "http://custom-zoning.internal:9001/evaluate" in received_urls
        assert "http://custom-fire.internal:9002/evaluate" in received_urls
        assert "http://custom-water.internal:9003/evaluate" in received_urls
        assert "http://custom-structural.internal:9004/evaluate" in received_urls

    asyncio.run(_test())


def test_trace_events_emission():
    """Verify that TraceEvent callback receives full saga lifecycle events."""
    async def _test():
        permit = load_sample_permit()
        events: List[TraceEvent] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            dept = body["department"]
            if dept == "water":
                # Fail once with 500, then succeed
                if body["attempt"] == 1:
                    return httpx.Response(500, json={"error": "fail"})
            return httpx.Response(200, json=make_eval_response(body["permit_id"], dept))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await dispatch_permit(
                permit=permit,
                trace_id="trc-test-events",
                client=client,
                on_event=events.append,
            )

        event_types = [e.type for e in events]
        assert TraceEventType.SPAN_STARTED in event_types
        assert TraceEventType.CALL_DISPATCHED in event_types
        assert TraceEventType.CALL_RETRYING in event_types
        assert TraceEventType.CALL_SUCCEEDED in event_types
        assert TraceEventType.SPAN_COMPLETED in event_types

        # Verify all events have consistent trace_id and permit_id
        for e in events:
            assert e.trace_id == "trc-test-events"
            assert e.permit_id == permit.permit_id

    asyncio.run(_test())


def test_dispatcher_class_interface():
    """Verify object-oriented Dispatcher class works as expected."""
    async def _test():
        permit = load_sample_permit()

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            return httpx.Response(200, json=make_eval_response(body["permit_id"], body["department"]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            dispatcher = Dispatcher(
                endpoints={"zoning": "http://z:8001", "fire": "http://f:8002", "water": "http://w:8003", "structural": "http://s:8004"},
                client=client,
            )
            results = await dispatcher.dispatch(permit)

        assert len(results) == 4
        assert all(r.status == DepartmentStatus.APPROVED for r in results.values())

    asyncio.run(_test())


def test_response_identity_mismatch_department_and_permit():
    """Verify department or permit_id mismatch returns ERROR, closes span with CALL_FAILED and SPAN_COMPLETED."""
    async def _test():
        permit = load_sample_permit()
        events_dept: List[TraceEvent] = []
        events_permit: List[TraceEvent] = []

        # 1. Department mismatch
        def dept_mismatch_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=make_eval_response(permit.permit_id or "p1", "fire", DepartmentStatus.APPROVED),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(dept_mismatch_handler)) as client:
            summary = await dispatch_department(
                dept_name="zoning",
                endpoint_url="http://zoning:8001/evaluate",
                permit=permit,
                permit_id=permit.permit_id or "p1",
                trace_id="t-id-mismatch-1",
                client=client,
                max_attempts=1,
                on_event=events_dept.append,
            )

        assert summary.status == DepartmentStatus.ERROR
        assert "Department response identity mismatch" in (summary.error_message or "")
        dept_event_types = [e.type for e in events_dept]
        assert TraceEventType.CALL_FAILED in dept_event_types
        assert TraceEventType.SPAN_COMPLETED in dept_event_types
        fail_evt = next(e for e in events_dept if e.type == TraceEventType.CALL_FAILED)
        span_evt = next(e for e in events_dept if e.type == TraceEventType.SPAN_COMPLETED)
        assert fail_evt.detail.get("final_status") == DepartmentStatus.ERROR.value
        assert span_evt.detail.get("status") == DepartmentStatus.ERROR.value

        # 2. Permit ID mismatch
        def permit_mismatch_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=make_eval_response("wrong-permit-id", "zoning", DepartmentStatus.APPROVED),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(permit_mismatch_handler)) as client:
            summary = await dispatch_department(
                dept_name="zoning",
                endpoint_url="http://zoning:8001/evaluate",
                permit=permit,
                permit_id=permit.permit_id or "p1",
                trace_id="t-id-mismatch-2",
                client=client,
                max_attempts=1,
                on_event=events_permit.append,
            )

        assert summary.status == DepartmentStatus.ERROR
        assert "Department response identity mismatch" in (summary.error_message or "")
        permit_event_types = [e.type for e in events_permit]
        assert TraceEventType.CALL_FAILED in permit_event_types
        assert TraceEventType.SPAN_COMPLETED in permit_event_types
        fail_evt2 = next(e for e in events_permit if e.type == TraceEventType.CALL_FAILED)
        span_evt2 = next(e for e in events_permit if e.type == TraceEventType.SPAN_COMPLETED)
        assert fail_evt2.detail.get("final_status") == DepartmentStatus.ERROR.value
        assert span_evt2.detail.get("status") == DepartmentStatus.ERROR.value

    asyncio.run(_test())


def test_nonterminal_status_emits_call_failed_and_span_completed():
    """Verify nonterminal status (e.g. PENDING) returns ERROR and emits CALL_FAILED and SPAN_COMPLETED."""
    async def _test():
        permit = load_sample_permit()
        events: List[TraceEvent] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=make_eval_response(permit.permit_id or "p1", "zoning", DepartmentStatus.PENDING),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            summary = await dispatch_department(
                dept_name="zoning",
                endpoint_url="http://zoning:8001/evaluate",
                permit=permit,
                permit_id=permit.permit_id or "p1",
                trace_id="t-nonterminal",
                client=client,
                max_attempts=1,
                on_event=events.append,
            )

        assert summary.status == DepartmentStatus.ERROR
        assert "nonterminal status" in (summary.error_message or "")
        event_types = [e.type for e in events]
        assert TraceEventType.CALL_FAILED in event_types
        assert TraceEventType.SPAN_COMPLETED in event_types
        fail_evt = next(e for e in events if e.type == TraceEventType.CALL_FAILED)
        span_evt = next(e for e in events if e.type == TraceEventType.SPAN_COMPLETED)
        assert fail_evt.detail.get("final_status") == DepartmentStatus.ERROR.value
        assert span_evt.detail.get("status") == DepartmentStatus.ERROR.value

    asyncio.run(_test())


def test_schema_validation_exception_emits_call_failed_and_span_completed():
    """Verify schema validation exception returns ERROR and emits CALL_FAILED and SPAN_COMPLETED."""
    async def _test():
        permit = load_sample_permit()
        events: List[TraceEvent] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"malformed": "schema", "status": "INVALID"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            summary = await dispatch_department(
                dept_name="water",
                endpoint_url="http://water:8003/evaluate",
                permit=permit,
                permit_id=permit.permit_id or "p1",
                trace_id="t-schema-err",
                client=client,
                max_attempts=1,
                on_event=events.append,
            )

        assert summary.status == DepartmentStatus.ERROR
        assert "Invalid response schema" in (summary.error_message or "")
        event_types = [e.type for e in events]
        assert TraceEventType.CALL_FAILED in event_types
        assert TraceEventType.SPAN_COMPLETED in event_types
        fail_evt = next(e for e in events if e.type == TraceEventType.CALL_FAILED)
        span_evt = next(e for e in events if e.type == TraceEventType.SPAN_COMPLETED)
        assert fail_evt.detail.get("final_status") == DepartmentStatus.ERROR.value
        assert span_evt.detail.get("status") == DepartmentStatus.ERROR.value

    asyncio.run(_test())


def test_async_callback_sequential_ordering():
    """Verify async callback is awaited sequentially and preserves trace event order."""
    async def _test():
        permit = load_sample_permit()
        received_events: List[Tuple[TraceEventType, float]] = []

        async def async_on_event(event: TraceEvent):
            await asyncio.sleep(0.01)
            received_events.append((event.type, time.perf_counter()))

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            return httpx.Response(200, json=make_eval_response(body["permit_id"], body["department"]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            summary = await dispatch_department(
                dept_name="fire",
                endpoint_url="http://fire:8002/evaluate",
                permit=permit,
                permit_id=permit.permit_id or "p1",
                trace_id="t-async-order",
                client=client,
                max_attempts=1,
                on_event=async_on_event,
            )

        assert summary.status == DepartmentStatus.APPROVED
        event_types = [t for t, _ in received_events]
        expected_types = [
            TraceEventType.SPAN_STARTED,
            TraceEventType.CALL_DISPATCHED,
            TraceEventType.CALL_SUCCEEDED,
            TraceEventType.SPAN_COMPLETED,
        ]
        assert event_types == expected_types
        timestamps = [ts for _, ts in received_events]
        assert timestamps == sorted(timestamps)

    asyncio.run(_test())
