"""Stub department service for testing orchestrator fan-out and fault handling."""

import asyncio
import time
from fastapi import FastAPI, HTTPException

from contract.models import (
    DepartmentEvaluationRequest,
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentStatus,
    EvaluationReason,
    FaultControlRequest,
    FaultMode,
)


def create_stub_app(department: DepartmentName | str | None = None) -> FastAPI:
    app = FastAPI(title="Stub Department Service")
    app.state.department = DepartmentName(department) if department else None
    app.state.faults: dict[str, FaultControlRequest] = {}
    app.state.default_fault = FaultControlRequest()

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.post("/fault", response_model=FaultControlRequest)
    async def set_fault(req: FaultControlRequest):
        if req.department:
            app.state.faults[req.department.value] = req
        else:
            app.state.default_fault = req
            app.state.faults.clear()
        return req

    @app.get("/fault", response_model=FaultControlRequest)
    async def get_fault(department: DepartmentName | None = None):
        target = department or app.state.department
        if target and target.value in app.state.faults:
            return app.state.faults[target.value]
        if target:
            d = app.state.default_fault
            return FaultControlRequest(
                department=target,
                mode=d.mode,
                delay_ms=d.delay_ms,
                error_code=d.error_code,
                error_message=d.error_message,
            )
        return app.state.default_fault

    @app.post("/evaluate", response_model=DepartmentEvaluationResponse)
    async def evaluate(req: DepartmentEvaluationRequest):
        target_dept = app.state.department or req.department
        if app.state.department and req.department != app.state.department:
            raise HTTPException(
                422,
                f"Request department {req.department.value} does not match {app.state.department.value}",
            )

        fc = app.state.faults.get(target_dept.value, app.state.default_fault)
        if fc.mode == FaultMode.OFFLINE:
            raise HTTPException(503, "Service offline")
        if fc.mode == FaultMode.ERROR:
            raise HTTPException(fc.error_code, fc.error_message)

        start = time.perf_counter()
        if fc.mode == FaultMode.SLOW:
            await asyncio.sleep(fc.delay_ms / 1000.0)

        elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)
        return DepartmentEvaluationResponse(
            permit_id=req.permit_id,
            department=target_dept,
            status=DepartmentStatus.APPROVED,
            execution_time_ms=elapsed_ms,
            reasons=[
                EvaluationReason(
                    rule_id="STUB-001",
                    rule_name="Stub Check",
                    passed=True,
                    message="Stub passed",
                )
            ],
        )

    return app


app = create_stub_app()
router = app.router
