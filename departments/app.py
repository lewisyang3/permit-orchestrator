"""One department scaffold, configured four ways by Compose.

Rule evaluation and the Gemini integration belong in the next T2 step.
Never return fabricated approvals while that implementation is absent.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException

from contract.models import DepartmentEvaluationRequest, DepartmentName


def create_app(department: str | None = None, rule_file: Path | None = None) -> FastAPI:
    name = DepartmentName(department or os.environ.get("DEPARTMENT", "fire"))
    path = rule_file or Path(os.environ.get("RULE_FILE", f"rules/{name.value}.yaml"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with path.open() as handle:
            pack = yaml.safe_load(handle)
        if pack.get("department") != name.value or not pack.get("rules"):
            raise ValueError(f"Rule pack does not match department {name.value}")
        ids = [rule["rule_id"] for rule in pack["rules"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate rule IDs")
        app.state.rule_pack = pack
        yield

    app = FastAPI(title=f"{name.value.title()} department scaffold", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "healthy", "department": name.value}

    @app.get("/rules")
    async def rules():
        pack = app.state.rule_pack
        return {
            "department": name.value,
            "rule_set_id": pack["rule_set_id"],
            "rule_count": len(pack["rules"]),
            "evaluation_ready": False,
        }

    @app.post("/evaluate")
    async def evaluate(request: DepartmentEvaluationRequest):
        if request.department != name:
            raise HTTPException(422, "Request department does not match this service")
        raise HTTPException(
            501,
            detail={
                "code": "EVALUATOR_NOT_IMPLEMENTED",
                "department": name.value,
                "message": "Service and rules are loaded; Gemini review is not implemented yet.",
            },
        )

    return app


app = create_app()
