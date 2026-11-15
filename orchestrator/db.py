"""
SQLite persistence for Permit Orchestrator (Task 5).

Provides schema creation with WAL mode, thread-safe connection management,
and clean data-access helpers for permits, department results, and append-only trace events.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

from contract.models import (
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    FinalDecision,
    Permit,
    PermitStatusResponse,
    SagaState,
    SpanId,
    TraceEvent,
    TraceEventType,
    format_iso_utc,
)

# Global lock for thread-safe SQLite operations
_DB_LOCK = threading.RLock()

# Default database path, configurable via environment or set_default_db_path()
_DEFAULT_DB_PATH: Path = Path(os.environ.get("DATABASE_PATH", "permits.db"))


def set_default_db_path(path: Union[Path, str]) -> Path:
    """Set the process-wide default SQLite database path."""
    global _DEFAULT_DB_PATH
    with _DB_LOCK:
        _DEFAULT_DB_PATH = Path(path)
        return _DEFAULT_DB_PATH


def get_default_db_path() -> Path:
    """Get the current process-wide default SQLite database path."""
    with _DB_LOCK:
        return _DEFAULT_DB_PATH


def resolve_db_path(db_path: Optional[Union[Path, str]] = None) -> Path:
    """Resolve database path, falling back to process default."""
    if db_path is None:
        return get_default_db_path()
    return Path(db_path)


@contextmanager
def get_connection(
    db_path: Optional[Union[Path, str]] = None,
) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager providing a thread-safe connection with WAL mode enabled.
    Commits automatically on normal exit, rolls back on exceptions.
    """
    resolved_path = resolve_db_path(db_path)
    path_str = str(resolved_path)

    # Ensure parent directory exists for file-backed databases
    if path_str != ":memory:":
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with _DB_LOCK:
        conn = sqlite3.connect(
            path_str,
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        try:
            if path_str != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def is_wal_mode(db_path: Optional[Union[Path, str]] = None) -> bool:
    """Check if WAL mode is active for the specified database."""
    resolved_path = resolve_db_path(db_path)
    if str(resolved_path) == ":memory:":
        return False
    with get_connection(resolved_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        row = cursor.fetchone()
        return bool(row and str(row[0]).lower() == "wal")


def init_db(db_path: Optional[Union[Path, str]] = None) -> Path:
    """
    Initialize SQLite schema with WAL mode and create tables:
      - `permits`
      - `department_results`
      - `trace_events` (append-only)
    Sets the initialized path as process-wide default if db_path was provided.
    """
    resolved_path = resolve_db_path(db_path)
    set_default_db_path(resolved_path)

    with get_connection(resolved_path) as conn:
        cursor = conn.cursor()

        # 1. permits table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS permits (
                permit_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                state TEXT NOT NULL,
                final_decision TEXT,
                decision_code TEXT,
                decision_summary TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                trace_id TEXT
            );
            """
        )

        # 2. department_results table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS department_results (
                permit_id TEXT NOT NULL,
                department TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                reasons TEXT,
                error_message TEXT,
                PRIMARY KEY (permit_id, department)
            );
            """
        )

        # 3. trace_events table (append-only)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_events (
                event_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                permit_id TEXT NOT NULL,
                span TEXT NOT NULL,
                parent_span TEXT,
                type TEXT NOT NULL,
                ts TEXT NOT NULL,
                detail TEXT
            );
            """
        )

        # Indexes for fast querying
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trace_events_permit_id
            ON trace_events (permit_id, ts);
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_department_results_permit_id
            ON department_results (permit_id);
            """
        )

    return resolved_path


def reset_db(db_path: Optional[Union[Path, str]] = None) -> None:
    """Truncate all rows in permits, department_results, and trace_events tables."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trace_events;")
        cursor.execute("DELETE FROM department_results;")
        cursor.execute("DELETE FROM permits;")


def _serialize_payload(payload: Union[str, Dict[str, Any], Permit]) -> str:
    """Serialize permit payload to JSON string."""
    if hasattr(payload, "model_dump_json"):
        return payload.model_dump_json()
    if isinstance(payload, dict):
        return json.dumps(payload)
    return str(payload)


def _extract_trace_id_from_payload(payload: Union[str, Dict[str, Any], Permit]) -> Optional[str]:
    """Attempt to extract trace_id from payload if present."""
    if hasattr(payload, "trace_id"):
        return getattr(payload, "trace_id")
    if isinstance(payload, dict):
        return payload.get("trace_id")
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed.get("trace_id")
        except Exception:
            pass
    return None


def save_permit(
    permit_id: str,
    payload: Union[str, Dict[str, Any], Permit],
    state: Union[str, SagaState] = SagaState.RECEIVED,
    created_at: Optional[Union[str, datetime]] = None,
    trace_id: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> None:
    """
    Save initial permit or update existing permit payload and state.
    Preserves decision information if the permit has already reached consensus.
    """
    payload_str = _serialize_payload(payload)
    state_val = state.value if hasattr(state, "value") else str(state)
    created_at_val = format_iso_utc(created_at or datetime.now(timezone.utc))

    effective_trace_id = trace_id or _extract_trace_id_from_payload(payload)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO permits (
                permit_id, trace_id, payload, state,
                final_decision, decision_code, decision_summary,
                created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(permit_id) DO UPDATE SET
                payload = excluded.payload,
                state = excluded.state,
                trace_id = COALESCE(excluded.trace_id, permits.trace_id),
                final_decision = COALESCE(permits.final_decision, excluded.final_decision),
                decision_code = COALESCE(permits.decision_code, excluded.decision_code),
                decision_summary = COALESCE(permits.decision_summary, excluded.decision_summary),
                created_at = COALESCE(permits.created_at, excluded.created_at),
                decided_at = COALESCE(permits.decided_at, excluded.decided_at);
            """,
            (
                permit_id,
                effective_trace_id,
                payload_str,
                state_val,
                FinalDecision.PENDING.value,
                DecisionCode.PENDING.value,
                None,
                created_at_val,
                None,
            ),
        )


def update_permit_state(
    permit_id: str,
    state: Union[str, SagaState],
    db_path: Optional[Union[Path, str]] = None,
) -> bool:
    """Update permit saga state (e.g., from RECEIVED to EVALUATING)."""
    state_val = state.value if hasattr(state, "value") else str(state)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE permits SET state = ? WHERE permit_id = ?",
            (state_val, permit_id),
        )
        return cursor.rowcount > 0


def update_permit_decision(
    permit_id: str,
    state: Union[str, SagaState],
    final_decision: Union[str, FinalDecision],
    decision_code: Optional[Union[str, DecisionCode]] = None,
    decision_summary: Optional[str] = None,
    decided_at: Optional[Union[str, datetime]] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> bool:
    """
    Update permit record with final consensus evaluation results.
    Sets state, final_decision, decision_code, decision_summary, and decided_at.
    """
    state_val = state.value if hasattr(state, "value") else str(state)
    final_decision_val = (
        final_decision.value if hasattr(final_decision, "value") else str(final_decision)
    )
    decision_code_val = (
        decision_code.value
        if hasattr(decision_code, "value")
        else (str(decision_code) if decision_code is not None else None)
    )
    decided_at_val = format_iso_utc(decided_at or datetime.now(timezone.utc))

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE permits
            SET state = ?,
                final_decision = ?,
                decision_code = ?,
                decision_summary = ?,
                decided_at = ?
            WHERE permit_id = ?;
            """,
            (
                state_val,
                final_decision_val,
                decision_code_val,
                decision_summary,
                decided_at_val,
                permit_id,
            ),
        )
        return cursor.rowcount > 0


def save_department_result(
    permit_id: str,
    department: Union[str, DepartmentName],
    status: Union[str, DepartmentStatus],
    attempts: int,
    latency_ms: float,
    reasons: Optional[Union[str, List[Any]]] = None,
    error_message: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> None:
    """Save or update department evaluation result row for a permit."""
    dept_val = department.value if hasattr(department, "value") else str(department)
    status_val = status.value if hasattr(status, "value") else str(status)

    if reasons is None:
        reasons_json = "[]"
    elif isinstance(reasons, str):
        reasons_json = reasons
    else:
        serialized = []
        for r in reasons:
            if hasattr(r, "model_dump"):
                serialized.append(r.model_dump(mode="json"))
            elif isinstance(r, dict):
                serialized.append(r)
            else:
                serialized.append(str(r))
        reasons_json = json.dumps(serialized)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO department_results (
                permit_id, department, status, attempts, latency_ms, reasons, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(permit_id, department) DO UPDATE SET
                status = excluded.status,
                attempts = excluded.attempts,
                latency_ms = excluded.latency_ms,
                reasons = excluded.reasons,
                error_message = excluded.error_message;
            """,
            (
                permit_id,
                dept_val,
                status_val,
                int(attempts),
                float(latency_ms),
                reasons_json,
                error_message,
            ),
        )


def append_trace_event(
    event: Union[TraceEvent, Dict[str, Any]],
    db_path: Optional[Union[Path, str]] = None,
) -> None:
    """Append a TraceEvent to the append-only trace_events table."""
    if isinstance(event, dict):
        event = TraceEvent.model_validate(event)

    event_id = str(event.event_id)
    trace_id = str(event.trace_id)
    permit_id = str(event.permit_id)
    span_val = event.span.value if hasattr(event.span, "value") else str(event.span)
    parent_span_val = str(event.parent_span) if event.parent_span is not None else None
    type_val = event.type.value if hasattr(event.type, "value") else str(event.type)
    ts_val = format_iso_utc(event.ts)
    detail_val = json.dumps(event.detail) if event.detail else "{}"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trace_events (
                event_id, trace_id, permit_id, span, parent_span, type, ts, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                event_id,
                trace_id,
                permit_id,
                span_val,
                parent_span_val,
                type_val,
                ts_val,
                detail_val,
            ),
        )


def append_trace_events(
    events: List[Union[TraceEvent, Dict[str, Any]]],
    db_path: Optional[Union[Path, str]] = None,
) -> None:
    """Batch append multiple TraceEvents in a single transaction."""
    rows = []
    for ev in events:
        if isinstance(ev, dict):
            ev = TraceEvent.model_validate(ev)
        rows.append(
            (
                str(ev.event_id),
                str(ev.trace_id),
                str(ev.permit_id),
                ev.span.value if hasattr(ev.span, "value") else str(ev.span),
                str(ev.parent_span) if ev.parent_span is not None else None,
                ev.type.value if hasattr(ev.type, "value") else str(ev.type),
                format_iso_utc(ev.ts),
                json.dumps(ev.detail) if ev.detail else "{}",
            )
        )

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO trace_events (
                event_id, trace_id, permit_id, span, parent_span, type, ts, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            rows,
        )


def _parse_reasons(reasons_str: Optional[str]) -> List[EvaluationReason]:
    """Parse JSON string of reasons into List[EvaluationReason]."""
    if not reasons_str:
        return []
    try:
        raw_list = json.loads(reasons_str)
        if not isinstance(raw_list, list):
            return []
        reasons = []
        for item in raw_list:
            if isinstance(item, EvaluationReason):
                reasons.append(item)
            elif isinstance(item, dict):
                reasons.append(EvaluationReason.model_validate(item))
        return reasons
    except Exception:
        return []


def _query_department_results(
    conn: sqlite3.Connection, permit_id: str
) -> Dict[str, DepartmentResultSummary]:
    """Internal helper to retrieve department results using an active connection."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT department, status, attempts, latency_ms, reasons, error_message
        FROM department_results
        WHERE permit_id = ?
        ORDER BY department ASC;
        """,
        (permit_id,),
    )
    rows = cursor.fetchall()
    results: Dict[str, DepartmentResultSummary] = {}
    for r in rows:
        dept = str(r["department"])
        raw_status = str(r["status"])
        status = (
            DepartmentStatus(raw_status)
            if raw_status in DepartmentStatus._value2member_map_
            else DepartmentStatus.ERROR
        )
        parsed_reasons = _parse_reasons(r["reasons"])
        results[dept] = DepartmentResultSummary(
            status=status,
            attempts=int(r["attempts"]),
            latency_ms=float(r["latency_ms"]),
            reasons=parsed_reasons,
            error_message=r["error_message"],
        )
    return results


def get_department_results(
    permit_id: str, db_path: Optional[Union[Path, str]] = None
) -> Dict[str, DepartmentResultSummary]:
    """Fetch all department results for a permit as Dict[str, DepartmentResultSummary]."""
    with get_connection(db_path) as conn:
        return _query_department_results(conn, permit_id)


def get_permit(
    permit_id: str, db_path: Optional[Union[Path, str]] = None
) -> Optional[PermitStatusResponse]:
    """
    Retrieve PermitStatusResponse for a permit_id, including its department_results.
    Returns None if permit_id is not found in permits table.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT permit_id, trace_id, payload, state, final_decision,
                   decision_code, decision_summary, created_at, decided_at
            FROM permits
            WHERE permit_id = ?;
            """,
            (permit_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        dept_results = _query_department_results(conn, permit_id)
        for dept in (
            DepartmentName.ZONING.value,
            DepartmentName.FIRE.value,
            DepartmentName.WATER.value,
            DepartmentName.STRUCTURAL.value,
        ):
            if dept not in dept_results:
                dept_results[dept] = DepartmentResultSummary(
                    status=DepartmentStatus.PENDING,
                    attempts=0,
                    latency_ms=0.0,
                    reasons=[],
                )

        # Resolve trace_id
        trace_id = row["trace_id"]
        if not trace_id:
            cursor.execute(
                "SELECT trace_id FROM trace_events WHERE permit_id = ? LIMIT 1;",
                (permit_id,),
            )
            te_row = cursor.fetchone()
            trace_id = te_row["trace_id"] if te_row and te_row["trace_id"] else permit_id

        # Resolve state
        state_str = str(row["state"])
        state = (
            SagaState(state_str)
            if state_str in SagaState._value2member_map_
            else SagaState.RECEIVED
        )

        # Resolve final_decision
        final_decision_str = row["final_decision"]
        final_decision = (
            FinalDecision(final_decision_str)
            if final_decision_str and final_decision_str in FinalDecision._value2member_map_
            else FinalDecision.PENDING
        )

        # Resolve decision_code
        decision_code_str = row["decision_code"]
        decision_code: Optional[DecisionCode] = None
        if decision_code_str and decision_code_str in DecisionCode._value2member_map_:
            decision_code = DecisionCode(decision_code_str)
        elif final_decision == FinalDecision.PENDING:
            decision_code = DecisionCode.PENDING

        return PermitStatusResponse(
            permit_id=str(row["permit_id"]),
            trace_id=str(trace_id),
            state=state,
            final_decision=final_decision,
            decision_code=decision_code,
            decision_summary=row["decision_summary"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            department_results=dept_results,
        )


def get_permit_raw_payload(
    permit_id: str, db_path: Optional[Union[Path, str]] = None
) -> Optional[str]:
    """Retrieve raw JSON string payload for a permit."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT payload FROM permits WHERE permit_id = ?;", (permit_id,))
        row = cursor.fetchone()
        return str(row["payload"]) if row else None


def get_permit_payload(
    permit_id: str, db_path: Optional[Union[Path, str]] = None
) -> Optional[Dict[str, Any]]:
    """Retrieve parsed JSON dictionary payload for a permit."""
    raw = get_permit_raw_payload(permit_id, db_path=db_path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def get_trace_events(
    permit_id: str, db_path: Optional[Union[Path, str]] = None
) -> List[TraceEvent]:
    """
    Retrieve all append-only TraceEvents for a permit in chronological (insertion) order.
    Guarantees full event replay.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT event_id, trace_id, permit_id, span, parent_span, type, ts, detail
            FROM trace_events
            WHERE permit_id = ?
            ORDER BY rowid ASC;
            """,
            (permit_id,),
        )
        rows = cursor.fetchall()
        events: List[TraceEvent] = []
        for r in rows:
            detail = {}
            if r["detail"]:
                try:
                    detail = json.loads(r["detail"])
                except Exception:
                    detail = {"raw": r["detail"]}

            span_val = r["span"]
            try:
                span = SpanId(span_val)
            except (ValueError, KeyError):
                span = span_val

            type_val = r["type"]
            try:
                event_type = TraceEventType(type_val)
            except (ValueError, KeyError):
                event_type = type_val

            events.append(
                TraceEvent(
                    event_id=str(r["event_id"]),
                    trace_id=str(r["trace_id"]),
                    permit_id=str(r["permit_id"]),
                    span=span,
                    parent_span=r["parent_span"],
                    type=event_type,
                    ts=r["ts"],
                    detail=detail,
                )
            )
        return events
