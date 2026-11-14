"""Local Compose connectivity/storage harness, not Person A's T1/T5 implementation.

Uses /demo endpoints to avoid claiming the production /submit/SSE contract.
Only this service accesses SQLite. Demo requests finish synchronously.
"""

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException

from contract.models import (
    DepartmentEvaluationResponse,
    DepartmentName,
    DepartmentStatus,
    Permit,
)


def create_app(db_path: Path | None = None, transport=None) -> FastAPI:
    path = db_path or Path(os.environ.get("DATABASE_PATH", "/data/permits.db"))
    departments = {
        dept.value: os.environ.get(
            f"{dept.value.upper()}_URL", f"http://{dept.value}:{8001 + index}"
        )
        for index, dept in enumerate(DepartmentName)
    }
    # Enum order is zoning/fire/water/structural, matching the shared port map.
    def connect():
        return sqlite3.connect(path, timeout=5)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS demo_permits "
                "(permit_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL)"
            )
        async with httpx.AsyncClient(timeout=2.0, transport=transport) as client:
            app.state.client = client
            yield

    app = FastAPI(title="Compose development orchestrator", lifespan=lifespan)

    @app.get("/health")
    async def health():
        with connect() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "healthy", "mode": "development_harness", "storage": "sqlite"}

    async def call_department(name, url, permit_id, trace_id, permit):
        started = perf_counter()
        try:
            response = await app.state.client.post(
                f"{url}/evaluate",
                json={"permit_id": permit_id, "trace_id": trace_id, "department": name,
                      "attempt": 1, "permit": permit.model_dump(mode="json")},
            )
            response.raise_for_status()
            result = DepartmentEvaluationResponse.model_validate(response.json())
            if result.department.value != name or result.permit_id != permit_id:
                raise ValueError("Department response identity mismatch")
            if result.status not in (DepartmentStatus.APPROVED, DepartmentStatus.REJECTED):
                raise ValueError("Department returned a nonterminal review")
            return name, {"status": result.status.value, "attempts": 1,
                          "latency_ms": (perf_counter() - started) * 1000,
                          "reasons": [r.model_dump(mode="json") for r in result.reasons]}
        except httpx.TimeoutException:
            error, status = "Department request exceeded 2 seconds", "TIMEOUT"
        except httpx.HTTPStatusError as exc:
            error, status = f"Department returned HTTP {exc.response.status_code}", "ERROR"
        except (httpx.RequestError, ValueError):
            error, status = "Department unavailable or returned an invalid response", "ERROR"
        return name, {"status": status, "attempts": 1,
                      "latency_ms": (perf_counter() - started) * 1000,
                      "reasons": [], "error_message": error}

    @app.post("/demo/submit")
    async def submit(permit: Permit):
        permit_id, trace_id = str(uuid4()), str(uuid4())
        permit = permit.model_copy(update={"permit_id": permit_id})
        results = dict(await asyncio.gather(*[
            call_department(name, url, permit_id, trace_id, permit)
            for name, url in departments.items()
        ]))
        statuses = [entry["status"] for entry in results.values()]
        incomplete = any(status in ("ERROR", "TIMEOUT") for status in statuses)
        approved = len(statuses) == 4 and all(status == "APPROVED" for status in statuses)
        result = {"permit_id": permit_id, "trace_id": trace_id,
                  "mode": "development_harness", "final_decision": "APPROVED" if approved else "REJECTED",
                  "decision_code": "INCOMPLETE_REVIEWS" if incomplete else (
                      "ALL_DEPARTMENTS_APPROVED" if approved else "DEPARTMENT_REJECTIONS"),
                  "department_results": results}
        with connect() as db:
            db.execute("INSERT INTO demo_permits VALUES (?, ?, ?)",
                       (permit_id, permit.model_dump_json(), json.dumps(result)))
        return result

    @app.get("/demo/permits/{permit_id}")
    async def get_permit(permit_id: str):
        with connect() as db:
            row = db.execute("SELECT result FROM demo_permits WHERE permit_id = ?", (permit_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Permit not found")
        return json.loads(row[0])

    return app


app = create_app()
