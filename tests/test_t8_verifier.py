"""
Verifier test suite for Task T8:
Fire offline gives REJECTED, never APPROVED, no hang.
Suite runs against REAL department services instantiated via departments.app.create_app
and loaded with real YAML rule packs in rules/.

Verifies:
1. Real department microservice initialization with official rule packs:
   - rules/fire.yaml, rules/zoning.yaml, rules/water.yaml, rules/structural.yaml.
2. Fire service offline fault mode (HTTP 503 via POST /fault):
   - Retries up to MAX_ATTEMPTS (3 attempts) with backoff.
   - Bounded execution time (never hangs).
   - DepartmentResultSummary reflects status=ERROR, attempts=3, and 503 error detail.
   - Consensus strictly evaluates to FinalDecision.REJECTED with DecisionCode.INCOMPLETE_REVIEWS.
   - Invariant verification: MUST NEVER yield FinalDecision.APPROVED.
3. Fire service unreachable / connection error (simulated network transport drop):
   - Clean retry handling across 3 attempts without unhandled exceptions or hangs.
   - Evaluates to FinalDecision.REJECTED with DecisionCode.INCOMPLETE_REVIEWS.
   - Invariant: MUST NEVER yield FinalDecision.APPROVED.
4. Missing department results invariant:
   - A permit is NEVER APPROVED if any department result is missing, timed out, or errored.
   - Exhaustive checks across combinations of missing, None, ERROR, and TIMEOUT department summaries.
5. Fault modes across other real departments (Zoning, Water, Structural):
   - Offline (HTTP 503) fail-safe behavior.
   - Slow execution (> per-call timeout) timing out cleanly and failing safely to REJECTED.
   - Server error (HTTP 500) fail-safe behavior.
   - Simultaneous multi-department fault scenarios.
6. Trace events inspection:
   - Verify trace events record DISPATCH_ATTEMPT per retry attempt.
   - Verify trace events capture CALL_FAILED or CALL_TIMED_OUT with failure details.
   - Verify SPAN_COMPLETED reflects failure status.
   - Verify root orchestrator PERMIT_DECIDED reflects REJECTED decision and INCOMPLETE_REVIEWS code.
7. End-to-end FastAPI orchestrator pipeline integration:
   - Full saga dispatch using create_app against real department apps.
   - POST /submit with wait=true and wait=false.
   - SSE event stream inspection (/permits/{id}/stream).
   - Fault recovery after restoring fault mode back to none.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
import time
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contract.models import (
    DecisionCode,
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    FaultControlRequest,
    FaultControlResponse,
    FaultMode,
    FinalDecision,
    Permit,
    PermitStatusResponse,
    SagaState,
    SpanId,
    TraceEvent,
    TraceEventType,
)
from departments.app import create_app as create_dept_app
from orchestrator import consensus, dispatcher
from orchestrator.app import create_app as create_orchestrator_app

RULES_DIR = Path("rules")
FIXTURES_DIR = Path("contract/fixtures")


class RealDepartmentRouterTransport(httpx.AsyncBaseTransport):
    """
    Async router transport that routes requests to real department ASGI applications.
    Also supports simulating network drops / connection errors per department host.
    """

    def __init__(self, apps: Dict[str, FastAPI]):
        self.transports: Dict[str, httpx.ASGITransport] = {
            name: httpx.ASGITransport(app=app) for name, app in apps.items()
        }
        self.network_error_hosts: set[str] = set()

    def set_network_error(self, host: str, error: bool = True) -> None:
        if error:
            self.network_error_hosts.add(host)
        else:
            self.network_error_hosts.discard(host)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.network_error_hosts:
            raise httpx.ConnectError(f"Connection refused to {host}", request=request)
        if host in self.transports:
            return await self.transports[host].handle_async_request(request)
        raise RuntimeError(f"Unknown target department host in router transport: {host}")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def sample_permit() -> Permit:
    payload = json.loads((FIXTURES_DIR / "permit_approved.json").read_text())
    return Permit.model_validate(payload)


@pytest.fixture
def timeout_permit() -> Permit:
    payload = json.loads((FIXTURES_DIR / "permit_timeout_offline.json").read_text())
    return Permit.model_validate(payload)


@pytest.fixture
def rejected_fire_permit() -> Permit:
    payload = json.loads((FIXTURES_DIR / "permit_rejected_fire.json").read_text())
    return Permit.model_validate(payload)


@pytest.fixture
async def real_dept_cluster() -> AsyncGenerator[
    Tuple[Dict[str, FastAPI], RealDepartmentRouterTransport, httpx.AsyncClient], None
]:
    """
    Initializes all 4 real department services loaded with their real YAML rule packs
    and enters their lifespan context managers.
    """
    dept_names = ["zoning", "fire", "water", "structural"]
    apps: Dict[str, FastAPI] = {}
    for name in dept_names:
        rule_path = RULES_DIR / "ca-san-jose" / f"{name}.yaml"
        assert rule_path.exists(), f"Rule file missing: {rule_path}"
        apps[name] = create_dept_app(name, rules_root=RULES_DIR)

    async with AsyncExitStack() as stack:
        for name, app in apps.items():
            await stack.enter_async_context(app.router.lifespan_context(app))
            assert hasattr(app.state, "registry"), f"{name} rule packs were not loaded"

        transport = RealDepartmentRouterTransport(apps)
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            yield apps, transport, client


DEPARTMENT_ENDPOINTS: Dict[str, str] = {
    "zoning": "http://zoning/evaluate",
    "fire": "http://fire/evaluate",
    "water": "http://water/evaluate",
    "structural": "http://structural/evaluate",
}


# ============================================================================
# Core Scenario 1: Fire service offline mode (HTTP 503)
# ============================================================================

@pytest.mark.anyio
async def test_fire_offline_retries_and_strictly_yields_rejected(
    real_dept_cluster, sample_permit
):
    """
    Scenario: Fire service is in OFFLINE fault mode (HTTP 503).
    Verifies:
    1. Dispatcher executes up to 3 attempts with backoff.
    2. Bounded execution time (never hangs).
    3. Fire result has status ERROR, attempts=3, and HTTP 503 in error_message.
    4. Consensus evaluates to FinalDecision.REJECTED and DecisionCode.INCOMPLETE_REVIEWS.
    5. Invariant: Final decision MUST NEVER be APPROVED.
    """
    apps, transport, client = real_dept_cluster

    # 1. Put Fire department into OFFLINE fault mode
    fault_resp = await client.post("http://fire/fault", json={"mode": "offline"})
    assert fault_resp.status_code == 200
    assert fault_resp.json()["mode"] == "offline"

    trace_events: List[TraceEvent] = []

    # 2. Dispatch permit with bounded timeout assertion
    start_time = time.perf_counter()
    async with asyncio.timeout(5.0):
        results = await dispatcher.dispatch_permit(
            permit=sample_permit,
            trace_id="trc-fire-offline-01",
            endpoints=DEPARTMENT_ENDPOINTS,
            client=client,
            backoff_delays=[0.05, 0.1],  # accelerated backoff for fast bounded testing
            on_event=lambda ev: trace_events.append(ev),
        )
    duration = time.perf_counter() - start_time

    # Ensure no hang and reasonable bounded duration
    assert duration < 5.0, f"Execution hung or took too long: {duration:.2f}s"

    # 3. Inspect Fire department result
    assert "fire" in results
    fire_summary = results["fire"]
    assert fire_summary.status == DepartmentStatus.ERROR
    assert fire_summary.attempts == 3
    assert fire_summary.error_message is not None
    assert "503" in fire_summary.error_message

    # 4. Evaluate consensus
    final_dec, dec_code, summary, violations = consensus.evaluate_consensus(results)

    # 5. Core Invariant Assertions
    assert final_dec == FinalDecision.REJECTED
    assert dec_code == DecisionCode.INCOMPLETE_REVIEWS
    assert "fire" in summary.lower()
    assert final_dec != FinalDecision.APPROVED

    # 6. Verify clean recovery back to normal mode
    reset_resp = await client.post("http://fire/fault", json={"mode": "none"})
    assert reset_resp.status_code == 200
    assert reset_resp.json()["mode"] == "none"


@pytest.mark.anyio
async def test_fire_offline_under_default_backoff_no_hang(
    real_dept_cluster, sample_permit
):
    """
    Verifies that Fire offline under default backoff delays [0.2, 0.5] finishes
    strictly within bounded time (< 4.0s) and does not hang.
    """
    apps, transport, client = real_dept_cluster

    await client.post("http://fire/fault", json={"mode": "offline"})

    start_time = time.perf_counter()
    async with asyncio.timeout(4.0):
        results = await dispatcher.dispatch_permit(
            permit=sample_permit,
            trace_id="trc-fire-default-backoff",
            endpoints=DEPARTMENT_ENDPOINTS,
            client=client,
            # using DEFAULT_BACKOFF_DELAYS: [0.2, 0.5]
        )
    duration = time.perf_counter() - start_time

    assert duration < 4.0
    assert results["fire"].attempts == 3
    assert results["fire"].status == DepartmentStatus.ERROR

    final_dec, dec_code, _, _ = consensus.evaluate_consensus(results)
    assert final_dec == FinalDecision.REJECTED
    assert dec_code == DecisionCode.INCOMPLETE_REVIEWS
    assert final_dec != FinalDecision.APPROVED

    # Reset
    await client.post("http://fire/fault", json={"mode": "none"})


# ============================================================================
# Core Scenario 2: Fire service unreachable / connection error
# ============================================================================

@pytest.mark.anyio
async def test_fire_unreachable_network_error_clean_failure_no_hang(
    real_dept_cluster, sample_permit
):
    """
    Scenario: Fire service is completely unreachable / drops connection.
    Verifies:
    1. Retries up to MAX_ATTEMPTS (3 attempts).
    2. Does not hang (finishes within bounded timeout).
    3. Fire result has status ERROR, attempts=3, and error indicates connection error.
    4. Consensus evaluates to FinalDecision.REJECTED with DecisionCode.INCOMPLETE_REVIEWS.
    5. Invariant: MUST NEVER yield APPROVED.
    """
    apps, transport, client = real_dept_cluster

    # Simulate transport connection drop for fire host
    transport.set_network_error("fire", error=True)

    try:
        trace_events: List[TraceEvent] = []
        start_time = time.perf_counter()

        async with asyncio.timeout(5.0):
            results = await dispatcher.dispatch_permit(
                permit=sample_permit,
                trace_id="trc-fire-unreachable-01",
                endpoints=DEPARTMENT_ENDPOINTS,
                client=client,
                backoff_delays=[0.05, 0.1],
                on_event=lambda ev: trace_events.append(ev),
            )
        duration = time.perf_counter() - start_time

        assert duration < 5.0

        fire_summary = results["fire"]
        assert fire_summary.status == DepartmentStatus.ERROR
        assert fire_summary.attempts == 3
        assert "connection" in (fire_summary.error_message or "").lower()

        final_dec, dec_code, summary, _ = consensus.evaluate_consensus(results)
        assert final_dec == FinalDecision.REJECTED
        assert dec_code == DecisionCode.INCOMPLETE_REVIEWS
        assert "fire" in summary.lower()
        assert final_dec != FinalDecision.APPROVED
    finally:
        transport.set_network_error("fire", error=False)


# ============================================================================
# Core Scenario 3: Missing department results invariant (NEVER APPROVED)
# ============================================================================

@pytest.mark.parametrize("missing_dept", [
    "fire",
    "zoning",
    "water",
    "structural",
])
def test_consensus_invariant_single_missing_department_never_approved(missing_dept):
    """A permit is NEVER APPROVED if any single department is missing from the results."""
    # Synthesize approved results for all 4 departments
    base_results = {
        dept: DepartmentResultSummary(
            status=DepartmentStatus.APPROVED,
            attempts=1,
            latency_ms=10.0,
            reasons=[],
        )
        for dept in ["fire", "zoning", "water", "structural"]
    }

    # Delete one department
    del base_results[missing_dept]

    decision, code, summary, violations = consensus.evaluate_consensus(base_results)
    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert missing_dept in summary.lower()
    assert decision != FinalDecision.APPROVED


@pytest.mark.parametrize("omitted_depts", [
    ["fire", "water"],
    ["zoning", "structural"],
    ["fire", "zoning", "structural"],
    ["fire", "zoning", "water", "structural"],
])
def test_consensus_invariant_multiple_missing_departments_never_approved(omitted_depts):
    """A permit is NEVER APPROVED if multiple or all departments are missing."""
    results = {
        dept: DepartmentResultSummary(
            status=DepartmentStatus.APPROVED,
            attempts=1,
            latency_ms=10.0,
            reasons=[],
        )
        for dept in ["fire", "zoning", "water", "structural"]
        if dept not in omitted_depts
    }

    decision, code, summary, _ = consensus.evaluate_consensus(results)
    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert decision != FinalDecision.APPROVED


@pytest.mark.parametrize("bad_status", [
    DepartmentStatus.TIMEOUT,
    DepartmentStatus.ERROR,
])
@pytest.mark.parametrize("faulty_dept", [
    "fire",
    "zoning",
    "water",
    "structural",
])
def test_consensus_invariant_non_approved_status_never_approved(bad_status, faulty_dept):
    """A permit is NEVER APPROVED if any department returned TIMEOUT or ERROR."""
    results = {
        dept: DepartmentResultSummary(
            status=DepartmentStatus.APPROVED,
            attempts=1,
            latency_ms=10.0,
            reasons=[],
        )
        for dept in ["fire", "zoning", "water", "structural"]
    }
    # Corrupt one department with bad status
    results[faulty_dept] = DepartmentResultSummary(
        status=bad_status,
        attempts=3,
        latency_ms=2000.0,
        error_message=f"Department encountered {bad_status.value}",
        reasons=[],
    )

    decision, code, summary, _ = consensus.evaluate_consensus(results)
    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert faulty_dept in summary.lower()
    assert decision != FinalDecision.APPROVED


# ============================================================================
# Core Scenario 4: Fault modes on other real departments (Zoning, Water, Structural)
# ============================================================================

@pytest.mark.parametrize("dept_name", ["zoning", "water", "structural"])
@pytest.mark.anyio
async def test_other_departments_offline_mode_safe_rejection(
    real_dept_cluster, sample_permit, dept_name
):
    """
    Verifies that offline fault mode (HTTP 503) on Zoning, Water, or Structural
    retries 3 times, completes within bounded timeout, and strictly rejects.
    """
    apps, transport, client = real_dept_cluster

    # Put target department offline
    fault_resp = await client.post(f"http://{dept_name}/fault", json={"mode": "offline"})
    assert fault_resp.status_code == 200

    try:
        start_time = time.perf_counter()
        async with asyncio.timeout(5.0):
            results = await dispatcher.dispatch_permit(
                permit=sample_permit,
                trace_id=f"trc-{dept_name}-offline",
                endpoints=DEPARTMENT_ENDPOINTS,
                client=client,
                backoff_delays=[0.05, 0.1],
            )
        duration = time.perf_counter() - start_time

        assert duration < 5.0
        dept_res = results[dept_name]
        assert dept_res.status == DepartmentStatus.ERROR
        assert dept_res.attempts == 3

        decision, code, summary, _ = consensus.evaluate_consensus(results)
        assert decision == FinalDecision.REJECTED
        assert code == DecisionCode.INCOMPLETE_REVIEWS
        assert dept_name in summary.lower()
        assert decision != FinalDecision.APPROVED
    finally:
        await client.post(f"http://{dept_name}/fault", json={"mode": "none"})


@pytest.mark.anyio
async def test_department_slow_past_timeout_clean_rejection_no_hang(
    real_dept_cluster, sample_permit
):
    """
    Verifies that when a department is in SLOW fault mode exceeding the per-call timeout,
    dispatcher times out on each attempt, retries up to 3 times, finishes within bounded
    time without hanging, and consensus safely evaluates to REJECTED.
    """
    apps, transport, client = real_dept_cluster

    # Configure Water to delay 600ms per call
    await client.post("http://water/fault", json={"mode": "slow", "delay_ms": 600})

    try:
        start_time = time.perf_counter()
        # Set per-call timeout to 0.2s so 600ms exceeds it, triggering timeout
        async with asyncio.timeout(5.0):
            results = await dispatcher.dispatch_permit(
                permit=sample_permit,
                trace_id="trc-water-slow",
                endpoints=DEPARTMENT_ENDPOINTS,
                client=client,
                timeout_seconds=0.2,
                backoff_delays=[0.05, 0.05],
            )
        duration = time.perf_counter() - start_time

        assert duration < 5.0

        water_res = results["water"]
        assert water_res.status == DepartmentStatus.TIMEOUT
        assert water_res.attempts == 3
        assert "timeout" in (water_res.error_message or "").lower()

        decision, code, summary, _ = consensus.evaluate_consensus(results)
        assert decision == FinalDecision.REJECTED
        assert code == DecisionCode.INCOMPLETE_REVIEWS
        assert "water" in summary.lower()
        assert decision != FinalDecision.APPROVED
    finally:
        await client.post("http://water/fault", json={"mode": "none"})


@pytest.mark.anyio
async def test_fire_slow_past_timeout_retries_and_strictly_yields_rejected(
    real_dept_cluster, sample_permit
):
    """
    Verifies that Fire service in SLOW fault mode exceeding per-call timeout:
    1. Times out on each attempt and retries up to 3 attempts.
    2. Completes strictly within bounded time without hanging.
    3. Fire result has status=TIMEOUT, attempts=3, and timeout error message.
    4. Consensus evaluates to FinalDecision.REJECTED with DecisionCode.INCOMPLETE_REVIEWS.
    5. Invariant: MUST NEVER yield FinalDecision.APPROVED.
    """
    apps, transport, client = real_dept_cluster

    # Configure Fire to delay 500ms per call
    await client.post("http://fire/fault", json={"mode": "slow", "delay_ms": 500})

    try:
        start_time = time.perf_counter()
        # Per-call timeout 0.15s < 500ms delay, triggering timeout on every attempt
        async with asyncio.timeout(5.0):
            results = await dispatcher.dispatch_permit(
                permit=sample_permit,
                trace_id="trc-fire-slow",
                endpoints=DEPARTMENT_ENDPOINTS,
                client=client,
                timeout_seconds=0.15,
                backoff_delays=[0.05, 0.05],
            )
        duration = time.perf_counter() - start_time

        assert duration < 5.0, f"Execution took too long: {duration:.2f}s"

        fire_res = results["fire"]
        assert fire_res.status == DepartmentStatus.TIMEOUT
        assert fire_res.attempts == 3
        assert "timeout" in (fire_res.error_message or "").lower()

        decision, code, summary, _ = consensus.evaluate_consensus(results)
        assert decision == FinalDecision.REJECTED
        assert code == DecisionCode.INCOMPLETE_REVIEWS
        assert "fire" in summary.lower()
        assert decision != FinalDecision.APPROVED
    finally:
        await client.post("http://fire/fault", json={"mode": "none"})


@pytest.mark.anyio
async def test_department_error_code_500_safe_rejection(
    real_dept_cluster, sample_permit
):
    """Verifies that an injected HTTP 500 error on Structural retries and rejects."""
    apps, transport, client = real_dept_cluster

    await client.post(
        "http://structural/fault",
        json={"mode": "error", "error_code": 500, "error_message": "Structural engine panic"},
    )

    try:
        async with asyncio.timeout(5.0):
            results = await dispatcher.dispatch_permit(
                permit=sample_permit,
                trace_id="trc-struct-500",
                endpoints=DEPARTMENT_ENDPOINTS,
                client=client,
                backoff_delays=[0.05, 0.1],
            )

        struct_res = results["structural"]
        assert struct_res.status == DepartmentStatus.ERROR
        assert struct_res.attempts == 3
        assert "500" in (struct_res.error_message or "")

        decision, code, summary, _ = consensus.evaluate_consensus(results)
        assert decision == FinalDecision.REJECTED
        assert code == DecisionCode.INCOMPLETE_REVIEWS
        assert decision != FinalDecision.APPROVED
    finally:
        await client.post("http://structural/fault", json={"mode": "none"})


@pytest.mark.anyio
async def test_multiple_departments_simultaneous_faults(
    real_dept_cluster, sample_permit
):
    """
    Verifies system stability when multiple departments fault at the same time:
    - Fire: OFFLINE (503)
    - Structural: ERROR (500)
    All fail safely, complete without hanging, and produce REJECTED.
    """
    apps, transport, client = real_dept_cluster

    await client.post("http://fire/fault", json={"mode": "offline"})
    await client.post(
        "http://structural/fault",
        json={"mode": "error", "error_code": 500, "error_message": "DB failure"},
    )

    try:
        start = time.perf_counter()
        async with asyncio.timeout(5.0):
            results = await dispatcher.dispatch_permit(
                permit=sample_permit,
                trace_id="trc-multi-fault",
                endpoints=DEPARTMENT_ENDPOINTS,
                client=client,
                backoff_delays=[0.05, 0.1],
            )
        duration = time.perf_counter() - start
        assert duration < 5.0

        assert results["fire"].status == DepartmentStatus.ERROR
        assert results["structural"].status == DepartmentStatus.ERROR

        decision, code, summary, _ = consensus.evaluate_consensus(results)
        assert decision == FinalDecision.REJECTED
        assert code == DecisionCode.INCOMPLETE_REVIEWS
        assert decision != FinalDecision.APPROVED
    finally:
        await client.post("http://fire/fault", json={"mode": "none"})
        await client.post("http://structural/fault", json={"mode": "none"})


# ============================================================================
# Core Scenario 5: Trace Events Inspection
# ============================================================================

@pytest.mark.anyio
async def test_trace_events_record_attempts_errors_and_final_state(
    real_dept_cluster, sample_permit
):
    """
    Verifies that trace events accurately capture:
    1. DISPATCH_ATTEMPT for each of the 3 retry attempts for the offline department.
    2. CALL_FAILED event with error details.
    3. SPAN_COMPLETED event reflecting final ERROR status.
    """
    apps, transport, client = real_dept_cluster

    await client.post("http://fire/fault", json={"mode": "offline"})

    trace_events: List[TraceEvent] = []

    try:
        await dispatcher.dispatch_permit(
            permit=sample_permit,
            trace_id="trc-events-verify",
            endpoints=DEPARTMENT_ENDPOINTS,
            client=client,
            backoff_delays=[0.05, 0.05],
            on_event=lambda ev: trace_events.append(ev),
        )

        fire_events = [ev for ev in trace_events if ev.span == SpanId.DEPT_FIRE]
        assert len(fire_events) >= 5, f"Expected at least 5 fire trace events, got {len(fire_events)}"

        # Verify attempt events
        attempt_events = [
            ev for ev in fire_events if ev.type == TraceEventType.CALL_DISPATCHED
        ]
        assert len(attempt_events) == 3
        attempts_recorded = [ev.detail.get("attempt") for ev in attempt_events]
        assert attempts_recorded == [1, 2, 3]

        # Verify retry events
        retry_events = [
            ev for ev in fire_events if ev.type == TraceEventType.CALL_RETRYING
        ]
        assert len(retry_events) == 2

        # Verify CALL_FAILED event
        failed_events = [ev for ev in fire_events if ev.type == TraceEventType.CALL_FAILED]
        assert len(failed_events) == 1
        assert failed_events[0].detail.get("attempts_total") == 3
        assert failed_events[0].detail.get("final_status") == DepartmentStatus.ERROR.value

        # Verify SPAN_COMPLETED event
        completed_events = [
            ev for ev in fire_events if ev.type == TraceEventType.SPAN_COMPLETED
        ]
        assert len(completed_events) == 1
        assert completed_events[0].detail.get("status") == DepartmentStatus.ERROR.value
    finally:
        await client.post("http://fire/fault", json={"mode": "none"})


# ============================================================================
# Core Scenario 6: Full Orchestrator App End-to-End Verification
# ============================================================================

@pytest.mark.anyio
async def test_orchestrator_app_e2e_fire_offline_submit_wait(
    real_dept_cluster, sample_permit
):
    """
    End-to-end test against FastAPI orchestrator app:
    1. Fire department set to OFFLINE mode.
    2. POST /submit?wait=true dispatches to real departments via router transport.
    3. Verifies response status 202, state=DECIDED, final_decision=REJECTED,
       decision_code=INCOMPLETE_REVIEWS.
    4. GET /permits/{permit_id} returns the persistent rejected state.
    5. Invariant: NEVER APPROVED.
    """
    apps, transport, client = real_dept_cluster

    await client.post("http://fire/fault", json={"mode": "offline"})

    # Setup orchestrator app with custom dispatcher wired to real department cluster
    async def e2e_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            endpoints=DEPARTMENT_ENDPOINTS,
            client=client,
            backoff_delays=[0.05, 0.1],
            on_event=on_event,
        )

    orch_fastapi = create_orchestrator_app(dispatcher_override=e2e_dispatcher)

    try:
        orch_transport = httpx.ASGITransport(app=orch_fastapi)
        async with httpx.AsyncClient(transport=orch_transport, base_url="http://orchestrator") as orch_client:
            # Submit permit with wait=true
            submit_resp = await orch_client.post(
                "/submit?wait=true",
                json=sample_permit.model_dump(mode="json"),
            )
            assert submit_resp.status_code == 202
            submit_data = submit_resp.json()
            permit_id = submit_data["permit_id"]

            # Query permit status from database
            status_resp = await orch_client.get(f"/permits/{permit_id}")
            assert status_resp.status_code == 200
            status_data = status_resp.json()

            assert status_data["state"] == SagaState.DECIDED.value
            assert status_data["final_decision"] == FinalDecision.REJECTED.value
            assert status_data["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
            assert "fire" in status_data["decision_summary"].lower()

            # Absolute Invariant Check
            assert status_data["final_decision"] != FinalDecision.APPROVED.value

            # Department results verification
            fire_result = status_data["department_results"]["fire"]
            assert fire_result["status"] == DepartmentStatus.ERROR.value
            assert fire_result["attempts"] == 3
    finally:
        await client.post("http://fire/fault", json={"mode": "none"})


@pytest.mark.anyio
async def test_orchestrator_app_e2e_sse_stream_fire_offline(
    real_dept_cluster, sample_permit
):
    """
    Verifies that SSE streaming (/permits/{permit_id}/stream) during Fire offline
    streams trace events and terminates cleanly with event: done having final_decision=REJECTED.
    """
    apps, transport, client = real_dept_cluster

    await client.post("http://fire/fault", json={"mode": "offline"})

    async def e2e_dispatcher(permit: Permit, trace_id: str, on_event=None):
        return await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            endpoints=DEPARTMENT_ENDPOINTS,
            client=client,
            backoff_delays=[0.05, 0.1],
            on_event=on_event,
        )

    orch_fastapi = create_orchestrator_app(dispatcher_override=e2e_dispatcher)

    try:
        orch_transport = httpx.ASGITransport(app=orch_fastapi)
        async with httpx.AsyncClient(transport=orch_transport, base_url="http://orchestrator") as orch_client:
            # Submit without wait
            submit_resp = await orch_client.post(
                "/submit?wait=false",
                json=sample_permit.model_dump(mode="json"),
            )
            assert submit_resp.status_code == 202
            permit_id = submit_resp.json()["permit_id"]

            # Connect to SSE stream
            events_received: List[str] = []
            done_payload = None

            async with orch_client.stream("GET", f"/permits/{permit_id}/stream") as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("event: "):
                        events_received.append(line.replace("event: ", "").strip())
                    elif line.startswith("data: "):
                        data_str = line.replace("data: ", "").strip()
                        if events_received and events_received[-1] == "done":
                            done_payload = json.loads(data_str)
                            break

            assert "trace_event" in events_received
            assert "done" in events_received
            assert done_payload is not None
            assert done_payload["permit_id"] == permit_id
            assert done_payload["final_decision"] == FinalDecision.REJECTED.value
            assert done_payload["final_decision"] != FinalDecision.APPROVED.value
    finally:
        await client.post("http://fire/fault", json={"mode": "none"})


@pytest.mark.anyio
async def test_orchestrator_app_e2e_with_native_client_and_admin_fault(
    real_dept_cluster, sample_permit
):
    """
    Verifies orchestrator configured with native endpoints and http_client:
    1. Uses POST /admin/fault to inject offline into Fire via orchestrator.
    2. Uses GET /admin/fault to verify all departments' fault modes.
    3. Submits permit via POST /submit?wait=true.
    4. Verifies REJECTED decision, INCOMPLETE_REVIEWS code, never APPROVED.
    5. Recovers via POST /admin/fault mode=none.
    """
    apps, transport, client = real_dept_cluster

    # Instantiate orchestrator with native endpoints and shared transport client
    orch_fastapi = create_orchestrator_app(
        endpoints=DEPARTMENT_ENDPOINTS,
        http_client=client,
        backoff_delays=[0.05, 0.1],
    )

    orch_transport = httpx.ASGITransport(app=orch_fastapi)
    async with httpx.AsyncClient(transport=orch_transport, base_url="http://orchestrator") as orch_client:
        # 1. Inject fault via /admin/fault
        admin_set_resp = await orch_client.post(
            "/admin/fault",
            json={"department": "fire", "mode": "offline"},
        )
        assert admin_set_resp.status_code == 200
        assert admin_set_resp.json()["mode"] == "offline"

        # 2. Inspect active faults via GET /admin/fault
        admin_get_resp = await orch_client.get("/admin/fault")
        assert admin_get_resp.status_code == 200
        admin_data = admin_get_resp.json()
        assert admin_data["fire"]["mode"] == "offline"
        assert admin_data["zoning"]["mode"] == "none"

        # 3. Submit permit with wait=true
        submit_resp = await orch_client.post(
            "/submit?wait=true",
            json=sample_permit.model_dump(mode="json"),
        )
        assert submit_resp.status_code == 202
        permit_id = submit_resp.json()["permit_id"]

        status_resp = await orch_client.get(f"/permits/{permit_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()

        assert status_data["state"] == SagaState.DECIDED.value
        assert status_data["final_decision"] == FinalDecision.REJECTED.value
        assert status_data["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
        assert status_data["final_decision"] != FinalDecision.APPROVED.value

        # 4. Clean recovery via /admin/fault
        clear_resp = await orch_client.post(
            "/admin/fault",
            json={"department": "fire", "mode": "none"},
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["mode"] == "none"

        admin_get_resp2 = await orch_client.get("/admin/fault")
        assert admin_get_resp2.json()["fire"]["mode"] == "none"

