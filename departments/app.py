"""Department review FastAPI microservice.

Configured per department via Compose / environment variables.
Evaluates permit applications against deterministic department YAML rule packs
and enriches findings with Gemini explanations (with guaranteed deterministic fallback).
Never returns fabricated approvals from partial starter packs or missing evidence.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from contract.models import (
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentStatus,
    FaultControlRequest,
    FaultControlResponse,
    FaultMode,
)
from departments.evaluator import (
    EvaluationOutcome,
    evaluate_department,
    load_and_validate_rule_pack,
)
from departments.explanations import generate_explanations


def create_app(department: str | None = None, rule_file: Path | None = None) -> FastAPI:
    name = DepartmentName(department or os.environ.get("DEPARTMENT", "fire"))
    path = rule_file or Path(os.environ.get("RULE_FILE", f"rules/{name.value}.yaml"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup lifespan: load and validate YAML rule pack for DEPARTMENT using evaluator.py.
        # Validate rule pack at startup and fail fast on malformed rules.
        pack = load_and_validate_rule_pack(path, expected_department=name)
        app.state.rule_pack = pack
        async with httpx.AsyncClient(timeout=1.2) as http_client:
            app.state.http_client = http_client
            yield

    app = FastAPI(title=f"{name.value.title()} department service", lifespan=lifespan)
    app.state.fault_mode = FaultControlRequest(department=name, mode=FaultMode.NONE)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "healthy", "department": name.value}

    @app.get("/fault", response_model=FaultControlRequest)
    async def get_fault():
        return app.state.fault_mode

    @app.post("/fault", response_model=FaultControlRequest)
    async def set_fault(req: FaultControlRequest):
        req.department = name
        app.state.fault_mode = req
        return req

    @app.get("/rules")
    async def rules() -> Dict[str, Any]:
        pack = app.state.rule_pack
        return {
            "department": name.value,
            "rule_set_id": pack["rule_set_id"],
            "rule_count": len(pack["rules"]),
            "evaluation_ready": True,
            "scope": pack.get("scope", {}),
            "notes": "Partial starter rules; approval disabled",
        }

    @app.post("/evaluate", response_model=DepartmentEvaluationResponse)
    async def evaluate(request: DepartmentEvaluationRequest) -> DepartmentEvaluationResponse:
        # Accepts DepartmentEvaluationRequest. Validate request.department matches this service (raise 422 if mismatched).
        if request.department != name:
            raise HTTPException(
                status_code=422,
                detail=f"Request department '{request.department.value}' does not match service department '{name.value}'",
            )

        fc = getattr(app.state, "fault_mode", None)
        if fc:
            if fc.mode == FaultMode.OFFLINE:
                raise HTTPException(status_code=503, detail="Department service offline")
            if fc.mode == FaultMode.ERROR:
                raise HTTPException(status_code=fc.error_code, detail=fc.error_message)
            if fc.mode == FaultMode.SLOW:
                await asyncio.sleep(fc.delay_ms / 1000.0)

        # Time execution using perf_counter().
        start_time = perf_counter()
        pack = app.state.rule_pack

        # Run deterministic evaluation via departments.evaluator.
        report = evaluate_department(rule_pack=pack, permit=request.permit)

        # Call departments.explanations to get explanations for findings (with fallback).
        http_client = getattr(app.state, "http_client", None)
        explanations = await generate_explanations(
            findings=report.findings,
            department=name.value,
            client=http_client,
        )
        report.apply_explanations(explanations)

        execution_time_ms = (perf_counter() - start_time) * 1000.0

        # If any finding is FAIL (verified violation): return HTTP 200 with DepartmentEvaluationResponse
        if report.is_rejected or any(f.outcome == EvaluationOutcome.FAIL for f in report.findings):
            return DepartmentEvaluationResponse(
                permit_id=request.permit_id,
                department=name,
                status=DepartmentStatus.REJECTED,
                execution_time_ms=execution_time_ms,
                reasons=report.reasons,
                evaluated_at=datetime.now(timezone.utc),
            )

        # If findings have no FAIL, but review is incomplete (due to allow_final_department_approval_from_this_pack_alone: false,
        # NEEDS_INFORMATION, or REQUIRES_HUMAN_REVIEW):
        # Return an explicit non-success HTTP response (HTTP 422) containing detail with a structured error code:
        if report.is_incomplete or not report.is_approved:
            unresolved = report.unresolved_reasons if report.unresolved_reasons else report.reasons
            reasons_payload = [r.model_dump(mode="json") for r in unresolved]
            findings_payload = [f.to_dict() for f in report.findings]
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INCOMPLETE_REVIEW",
                    "department": name.value,
                    "message": "Evaluation incomplete: partial pack policy or missing facts require human review",
                    "unresolved_reasons": reasons_payload,
                    "findings": findings_payload,
                    "execution_time_ms": execution_time_ms,
                },
            )

        # Complete rule pack approval branch
        return DepartmentEvaluationResponse(
            permit_id=request.permit_id,
            department=name,
            status=DepartmentStatus.APPROVED,
            execution_time_ms=execution_time_ms,
            reasons=report.reasons,
            evaluated_at=datetime.now(timezone.utc),
        )

    return app


app = create_app()
