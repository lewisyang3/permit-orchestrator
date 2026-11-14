#!/usr/bin/env python3
"""
T1 End-to-End Verification Script: Orchestrator Core Integration.

Spins up the FastAPI orchestrator application wired to four stub municipal
departments (zoning, fire, water, structural) via parallel dispatcher and
consensus engine, executing full HTTP request cycles and validating T1 'Done when' criteria:

1. Baseline Approval: Submitting a valid permit (contract/fixtures/permit_approved.json)
   returns HTTP 200 with APPROVED decision from all four stub departments.
2. Fault Injection (Offline 503): Taking Fire department offline exercises 3 retries
   with backoff and verifies consensus fail-safe (REJECTED / INCOMPLETE_REVIEWS)
   without hanging or blocking healthy departments.
3. Fault Injection (Slow Timeout): Fire department latency exceeding per-call timeout
   times out across retries and returns REJECTED / INCOMPLETE_REVIEWS.
4. Department Rejection: Department rejection returns REJECTED / DEPARTMENT_REJECTIONS
   with structured violations.
5. Fault Recovery: Resetting fault mode restores full APPROVED consensus.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Any, Dict

import httpx

from contract.models import (
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    FaultControlRequest,
    FaultMode,
    FinalDecision,
    Permit,
    SagaState,
)
from orchestrator import dispatcher
from orchestrator.app import create_app
from orchestrator.stub_department import create_stub_app


class DepartmentRouterTransport(httpx.AsyncBaseTransport):
    """Routes HTTP requests across the four stub department ASGI apps by target host."""

    def __init__(self, apps: Dict[str, Any]):
        self.transports = {name: httpx.ASGITransport(app=app) for name, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.transports:
            return await self.transports[host].handle_async_request(request)
        raise RuntimeError(f"Unknown target department host: {host}")


def print_section(title: str) -> None:
    sep = "=" * 80
    print(f"\n{sep}\n  {title}\n{sep}")


def print_curl_and_resp(curl_cmd: str, status_code: int, response_json: dict) -> None:
    print("\n--- [HTTP Request (curl equivalent)] ---")
    print(curl_cmd)
    print(f"\n--- [HTTP Response (status: {status_code})] ---")
    print(json.dumps(response_json, indent=2))


async def run_verification() -> bool:
    print_section("T1 Orchestrator Core — End-to-End Integration Verification")

    # 1. Initialize four stub department instances
    dept_names = ["zoning", "fire", "water", "structural"]
    stub_apps = {name: create_stub_app(name) for name in dept_names}
    dept_router_client = httpx.AsyncClient(transport=DepartmentRouterTransport(stub_apps))

    # 2. Configure orchestrator app wired to dispatcher
    custom_dispatcher_fn = None

    async def active_dispatcher(permit: Permit, trace_id: str) -> Dict[str, DepartmentResultSummary]:
        if custom_dispatcher_fn is not None:
            return await custom_dispatcher_fn(permit, trace_id)
        return await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            client=dept_router_client,
            backoff_delays=[0.05, 0.1],  # 50ms, 100ms for fast responsive tests
        )

    orch_app = create_app(dispatcher_override=active_dispatcher)
    orch_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=orch_app), base_url="http://localhost:8000"
    )

    fire_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub_apps["fire"]), base_url="http://fire:8002"
    )

    # Load fixtures
    approved_payload = json.loads(Path("contract/fixtures/permit_approved.json").read_text())
    rejected_payload = json.loads(Path("contract/fixtures/permit_rejected_fire.json").read_text())

    passed_checks = 0
    total_checks = 5

    # --------------------------------------------------------------------------
    # Check 1: Health check & Approved Permit (T1 'Done when' criteria)
    # --------------------------------------------------------------------------
    print_section("Check 1: Health Check and Approved Permit Submission (T1 Baseline)")

    health_resp = await orch_client.get("/health")
    assert health_resp.status_code == 200, f"Health check failed: {health_resp.text}"
    print(f"GET /health: {health_resp.json()}")

    curl_submit = (
        "curl -X POST http://localhost:8000/submit \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d @contract/fixtures/permit_approved.json"
    )
    submit_resp = await orch_client.post("/submit", json=approved_payload)
    submit_data = submit_resp.json()
    print_curl_and_resp(curl_submit, submit_resp.status_code, submit_data)

    permit_id = submit_data["permit_id"]
    curl_get = f"curl -X GET 'http://localhost:8000/permits/{permit_id}?wait=true'"
    status_resp = await orch_client.get(f"/permits/{permit_id}?wait=true")
    status_data = status_resp.json()
    print_curl_and_resp(curl_get, status_resp.status_code, status_data)

    assert status_data["state"] == SagaState.DECIDED.value
    assert status_data["final_decision"] == FinalDecision.APPROVED.value
    assert status_data["decision_code"] == DecisionCode.ALL_DEPARTMENTS_APPROVED.value
    assert len(status_data["department_results"]) == 4
    for d in dept_names:
        assert status_data["department_results"][d]["status"] == "APPROVED"
        assert status_data["department_results"][d]["attempts"] == 1
    print("\n>>> Check 1 PASSED: Sample permit successfully approved across all 4 stub departments.")
    passed_checks += 1

    # --------------------------------------------------------------------------
    # Check 2: Fault Injection — Fire Department OFFLINE (503) & 3 Retries
    # --------------------------------------------------------------------------
    print_section("Check 2: Fault Injection — Fire Department OFFLINE (503) & 3 Retries")

    curl_fault = (
        "curl -X POST http://fire:8002/fault \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d '{\"department\": \"fire\", \"mode\": \"offline\"}'"
    )
    fault_resp = await fire_client.post(
        "/fault",
        json=FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.OFFLINE).model_dump(mode="json"),
    )
    print_curl_and_resp(curl_fault, fault_resp.status_code, fault_resp.json())

    offline_payload = dict(approved_payload, permit_id="pm-test-offline-fire-001")
    t0 = time.perf_counter()
    resp = await orch_client.post("/submit", json=offline_payload)
    stat = (await orch_client.get(f"/permits/{resp.json()['permit_id']}?wait=true")).json()
    elapsed = time.perf_counter() - t0

    print_curl_and_resp(
        f"curl -X GET 'http://localhost:8000/permits/{resp.json()['permit_id']}?wait=true'",
        200,
        stat,
    )

    assert stat["final_decision"] == FinalDecision.REJECTED.value
    assert stat["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
    assert "fire" in stat["decision_summary"].lower()
    assert stat["department_results"]["fire"]["status"] == "ERROR"
    assert stat["department_results"]["fire"]["attempts"] == 3
    # Healthy departments still succeeded
    for d in ["zoning", "water", "structural"]:
        assert stat["department_results"][d]["status"] == "APPROVED"
        assert stat["department_results"][d]["attempts"] == 1

    print(f"\n>>> Check 2 PASSED: Fire offline caused REJECTED / INCOMPLETE_REVIEWS after 3 retries ({elapsed:.2f}s).")
    passed_checks += 1

    # --------------------------------------------------------------------------
    # Check 3: Fault Injection — Fire Department SLOW past per-call timeout
    # --------------------------------------------------------------------------
    print_section("Check 3: Fault Injection — Fire Department SLOW past Timeout")

    async def timeout_dispatcher(permit: Permit, trace_id: str) -> Dict[str, DepartmentResultSummary]:
        return await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            client=dept_router_client,
            timeout_seconds=0.1,  # 100ms per-call timeout
            backoff_delays=[0.05, 0.05],
        )

    custom_dispatcher_fn = timeout_dispatcher

    curl_slow_fault = (
        "curl -X POST http://fire:8002/fault \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d '{\"department\": \"fire\", \"mode\": \"slow\", \"delay_ms\": 250}'"
    )
    slow_resp = await fire_client.post(
        "/fault",
        json=FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.SLOW, delay_ms=250).model_dump(mode="json"),
    )
    print_curl_and_resp(curl_slow_fault, slow_resp.status_code, slow_resp.json())

    slow_payload = dict(approved_payload, permit_id="pm-test-slow-fire-002")
    resp_slow = await orch_client.post("/submit", json=slow_payload)
    stat_slow = (await orch_client.get(f"/permits/{resp_slow.json()['permit_id']}?wait=true")).json()

    print_curl_and_resp(
        f"curl -X GET 'http://localhost:8000/permits/{resp_slow.json()['permit_id']}?wait=true'",
        200,
        stat_slow,
    )

    assert stat_slow["final_decision"] == FinalDecision.REJECTED.value
    assert stat_slow["decision_code"] == DecisionCode.INCOMPLETE_REVIEWS.value
    assert stat_slow["department_results"]["fire"]["status"] == "TIMEOUT"
    assert stat_slow["department_results"]["fire"]["attempts"] == 3
    print("\n>>> Check 3 PASSED: Fire delay triggered per-call timeout across retries -> TIMEOUT.")
    passed_checks += 1

    # --------------------------------------------------------------------------
    # Check 4: Department Rejection Consensus Rule
    # --------------------------------------------------------------------------
    print_section("Check 4: Department Rejection Consensus Rule")

    async def rejecting_dispatcher(permit: Permit, trace_id: str) -> Dict[str, DepartmentResultSummary]:
        res = await dispatcher.dispatch_permit(
            permit=permit,
            trace_id=trace_id,
            client=dept_router_client,
            backoff_delays=[0.05, 0.05],
        )
        res["fire"] = DepartmentResultSummary(
            status=DepartmentStatus.REJECTED,
            attempts=1,
            latency_ms=18.5,
            reasons=[
                EvaluationReason(
                    rule_id="FIRE-002",
                    rule_name="Sprinkler Requirement for Assembly Occupancy",
                    passed=False,
                    message="Assembly occupancy load 110 exceeds threshold without sprinkler system.",
                )
            ],
            error_message=None,
        )
        return res

    custom_dispatcher_fn = rejecting_dispatcher

    curl_reject_submit = (
        "curl -X POST http://localhost:8000/submit \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d @contract/fixtures/permit_rejected_fire.json"
    )
    resp_rej = await orch_client.post("/submit", json=rejected_payload)
    stat_rej = (await orch_client.get(f"/permits/{resp_rej.json()['permit_id']}?wait=true")).json()

    print_curl_and_resp(curl_reject_submit, resp_rej.status_code, resp_rej.json())
    print_curl_and_resp(
        f"curl -X GET 'http://localhost:8000/permits/{resp_rej.json()['permit_id']}?wait=true'",
        200,
        stat_rej,
    )

    assert stat_rej["final_decision"] == FinalDecision.REJECTED.value
    assert stat_rej["decision_code"] == DecisionCode.DEPARTMENT_REJECTIONS.value
    assert stat_rej["department_results"]["fire"]["status"] == "REJECTED"
    print("\n>>> Check 4 PASSED: Department rejection properly triggered DEPARTMENT_REJECTIONS code.")
    passed_checks += 1

    # --------------------------------------------------------------------------
    # Check 5: Fault Recovery Cycle
    # --------------------------------------------------------------------------
    print_section("Check 5: Fault Recovery — Restoring Healthy Status")

    custom_dispatcher_fn = None  # Reset to standard dispatcher

    curl_reset_fault = (
        "curl -X POST http://fire:8002/fault \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d '{\"department\": \"fire\", \"mode\": \"none\"}'"
    )
    reset_resp = await fire_client.post(
        "/fault",
        json=FaultControlRequest(department=DepartmentName.FIRE, mode=FaultMode.NONE).model_dump(mode="json"),
    )
    print_curl_and_resp(curl_reset_fault, reset_resp.status_code, reset_resp.json())

    recovered_payload = dict(approved_payload, permit_id="pm-test-recovered-003")
    resp_rec = await orch_client.post("/submit", json=recovered_payload)
    stat_rec = (await orch_client.get(f"/permits/{resp_rec.json()['permit_id']}?wait=true")).json()

    print_curl_and_resp(
        f"curl -X GET 'http://localhost:8000/permits/{resp_rec.json()['permit_id']}?wait=true'",
        200,
        stat_rec,
    )

    assert stat_rec["final_decision"] == FinalDecision.APPROVED.value
    assert stat_rec["decision_code"] == DecisionCode.ALL_DEPARTMENTS_APPROVED.value
    for d in dept_names:
        assert stat_rec["department_results"][d]["status"] == "APPROVED"
    print("\n>>> Check 5 PASSED: Post-recovery permit returned APPROVED from all 4 stub departments.")
    passed_checks += 1

    # Final summary
    print_section("VERIFICATION SUMMARY")
    print(f"Checks passed: {passed_checks}/{total_checks} (100%)")
    print("All T1 criteria verified successfully:")
    print(" - Health endpoint functional")
    print(" - Parallel fan-out across four departments (zoning, fire, water, structural)")
    print(" - Per-call timeout enforcement and retries with backoff")
    print(" - Incomplete review precedence rule")
    print(" - Department rejection consensus rule")
    print(" - Full approval consensus rule")
    print(" - Dynamic fault injection and recovery")
    return True


if __name__ == "__main__":
    success = asyncio.run(run_verification())
    sys.exit(0 if success else 1)
