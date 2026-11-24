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
from pydantic import BaseModel

from contract.models import (
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentStatus,
    EvaluationReason,
    FaultControlRequest,
    FaultControlResponse,
    FaultMode,
    RuleSeverity,
)
from departments.evaluator import (
    EvaluationOutcome,
    evaluate_department,
)
from departments.explanations import generate_explanations
from departments.jurisdictions import load_registry, single_pack_registry


class DepartmentFaultStatus(BaseModel):
    department: DepartmentName
    mode: FaultMode
    delay_ms: int = 3000
    error_code: int = 500
    error_message: str = "Simulated fault injection"
    message: str = ""


def create_app(
    department: str | None = None,
    rule_file: Path | None = None,
    rules_root: Path | None = None,
) -> FastAPI:
    """Build a department service.

    By default it loads every city pack under RULES_ROOT (default "rules") and picks
    the pack per request from the permit's city. Passing rule_file (or setting
    RULE_FILE) instead applies that one pack to every permit; that is for tests only.
    """
    name = DepartmentName(department or os.environ.get("DEPARTMENT", "fire"))
    single_file = rule_file or (Path(os.environ["RULE_FILE"]) if os.environ.get("RULE_FILE") else None)
    root = rules_root or Path(os.environ.get("RULES_ROOT", "rules"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Load and validate every rule pack at startup; fail fast on malformed rules.
        if single_file is not None:
            registry = single_pack_registry(single_file, name)
        else:
            registry = load_registry(root, name)
        app.state.registry = registry
        async with httpx.AsyncClient(timeout=1.2) as http_client:
            app.state.http_client = http_client
            yield

    app = FastAPI(title=f"{name.value.title()} department service", lifespan=lifespan)
    app.state.fault_mode = DepartmentFaultStatus(
        department=name,
        mode=FaultMode.NONE,
        delay_ms=3000,
        error_code=500,
        error_message="Simulated fault injection",
        message=f"{name.value.title()} department operating normally",
    )

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

    @app.get("/fault", response_model=DepartmentFaultStatus)
    async def get_fault(department: DepartmentName | None = None) -> DepartmentFaultStatus:
        if department is not None and department != name:
            raise HTTPException(
                status_code=422,
                detail=f"Query department '{department.value}' does not match service department '{name.value}'",
            )
        return app.state.fault_mode

    @app.post("/fault", response_model=DepartmentFaultStatus)
    async def set_fault(req: FaultControlRequest) -> DepartmentFaultStatus:
        if req.department is not None and req.department != name:
            raise HTTPException(
                status_code=422,
                detail=f"Target department '{req.department.value}' does not match service department '{name.value}'",
            )
        if req.delay_ms < 0:
            raise HTTPException(
                status_code=422,
                detail="delay_ms must be non-negative",
            )
        status = DepartmentFaultStatus(
            department=name,
            mode=req.mode,
            delay_ms=req.delay_ms,
            error_code=req.error_code,
            error_message=req.error_message,
            message=(
                f"{name.value.title()} department operating normally"
                if req.mode == FaultMode.NONE
                else f"Fault mode for {name.value} set to {req.mode.value}"
            ),
        )
        app.state.fault_mode = status
        return status

    @app.get("/rules")
    async def rules() -> Dict[str, Any]:
        registry = app.state.registry
        return {
            "department": name.value,
            "jurisdictions": [j.summary() for j in registry.jurisdictions.values()],
            "evaluation_ready": True,
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

        # Time execution using perf_counter() starting before potential simulated sleep
        start_time = perf_counter()

        fc = getattr(app.state, "fault_mode", None)
        if fc:
            if fc.mode == FaultMode.OFFLINE:
                raise HTTPException(status_code=503, detail="Department service offline")
            if fc.mode == FaultMode.ERROR:
                raise HTTPException(status_code=fc.error_code, detail=fc.error_message)
            if fc.mode == FaultMode.SLOW:
                await asyncio.sleep(fc.delay_ms / 1000.0)

        registry = app.state.registry
        jurisdiction = registry.resolve(request.permit.property_address)
        if jurisdiction is None:
            # Don't name the cities that are supported: each city is a separate customer.
            addr = request.permit.property_address
            reason = EvaluationReason(
                rule_id="UNSUPPORTED_JURISDICTION",
                rule_name="Jurisdiction has no rule pack",
                passed=False,
                severity=RuleSeverity.ADVISORY,
                message=(
                    f"No {name.value} rules are loaded for {addr.city}, {addr.state}. "
                    "A reviewer must evaluate this permit against that city's own requirements."
                ),
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "UNSUPPORTED_JURISDICTION",
                    "department": name.value,
                    "message": reason.message,
                    "unresolved_reasons": [reason.model_dump(mode="json")],
                    "findings": [],
                    "execution_time_ms": (perf_counter() - start_time) * 1000.0,
                },
            )
        pack = jurisdiction.pack

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
                jurisdiction_id=jurisdiction.jurisdiction_id,
                rule_set_id=pack["rule_set_id"],
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
                    "jurisdiction_id": jurisdiction.jurisdiction_id,
                    "rule_set_id": pack["rule_set_id"],
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
            jurisdiction_id=jurisdiction.jurisdiction_id,
            rule_set_id=pack["rule_set_id"],
            status=DepartmentStatus.APPROVED,
            execution_time_ms=execution_time_ms,
            reasons=report.reasons,
            evaluated_at=datetime.now(timezone.utc),
        )

    return app


app = create_app()
