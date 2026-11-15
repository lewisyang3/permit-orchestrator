"""
Unit and integration tests for SQLite persistence in Permit Orchestrator.
Tests orchestrator/db.py schema, WAL mode, CRUD, append-only guarantees,
and concurrent multi-threaded execution.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from contract.models import (
    Applicant,
    ApplicantRole,
    ConstructionType,
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    FinalDecision,
    FireData,
    FoundationType,
    OccupancyType,
    Permit,
    PermitStatusResponse,
    PropertyAddress,
    RuleSeverity,
    SagaState,
    SeismicDesignCategory,
    SpanId,
    StructuralData,
    TraceEvent,
    TraceEventType,
    WaterData,
    ZoneCode,
    ZoningData,
)
from orchestrator.db import (
    append_trace_event,
    append_trace_events,
    get_connection,
    get_default_db_path,
    get_department_results,
    get_permit,
    get_permit_payload,
    get_permit_raw_payload,
    get_trace_events,
    init_db,
    is_wal_mode,
    reset_db,
    save_department_result,
    save_permit,
    set_default_db_path,
    update_permit_decision,
    update_permit_state,
)


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    """Fixture providing an isolated temporary database path for each test."""
    db_file = tmp_path / "test_permits.db"
    init_db(db_file)
    return db_file


@pytest.fixture
def sample_permit() -> Permit:
    """Fixture providing a realistic valid Permit object."""
    return Permit(
        permit_id="permit-test-100",
        project_title="Two-Story Single Family Residence",
        project_description="New residential construction with sprinkler system",
        estimated_cost_usd=450000.0,
        property_address=PropertyAddress(
            street="123 Evergreen Terrace",
            city="Springfield",
            state="OR",
            zip="97477",
        ),
        applicant=Applicant(
            name="Homer Simpson",
            role=ApplicantRole.HOMEOWNER,
            email="homer@springfield.org",
            phone="555-7334",
        ),
        zoning=ZoningData(
            zone_code=ZoneCode.R1,
            building_height_ft=28.0,
            lot_area_sqft=7500.0,
            building_footprint_sqft=2200.0,
            setback_front_ft=25.0,
            setback_rear_ft=20.0,
            setback_side_ft=8.0,
        ),
        fire=FireData(
            occupancy_type=OccupancyType.RESIDENTIAL,
            occupant_load=6,
            has_sprinkler_system=True,
            egress_exits_count=3,
            distance_to_hydrant_ft=120.0,
            has_fire_alarm=True,
        ),
        water=WaterData(
            water_meter_size_inches=1.0,
            daily_water_usage_gpd=250.0,
            has_backflow_preventer=True,
            has_stormwater_plan=True,
        ),
        structural=StructuralData(
            stories_count=2,
            construction_type=ConstructionType.WOOD_FRAME,
            seismic_design_category=SeismicDesignCategory.C,
            foundation_type=FoundationType.CONTINUOUS_SPREAD_FOOTING,
        ),
    )


# =========================================================================
# 1. Schema & WAL Mode Tests
# =========================================================================

def test_init_db_creates_tables_and_indexes(test_db_path: Path):
    """Verify tables and indexes exist after init_db."""
    with get_connection(test_db_path) as conn:
        cursor = conn.cursor()

        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row["name"] for row in cursor.fetchall()}
        assert "permits" in tables
        assert "department_results" in tables
        assert "trace_events" in tables

        # Check indexes
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indexes = {row["name"] for row in cursor.fetchall()}
        assert "idx_trace_events_permit_id" in indexes
        assert "idx_department_results_permit_id" in indexes


def test_wal_mode_enabled(test_db_path: Path):
    """Verify file-backed SQLite database has WAL mode enabled."""
    assert is_wal_mode(test_db_path) is True


def test_init_db_idempotent(test_db_path: Path):
    """Calling init_db multiple times on the same path is safe and idempotent."""
    path1 = init_db(test_db_path)
    path2 = init_db(test_db_path)
    assert path1 == path2
    assert is_wal_mode(test_db_path) is True


def test_reset_db_clears_tables(test_db_path: Path, sample_permit: Permit):
    """reset_db clears rows without dropping tables."""
    save_permit("p-1", sample_permit, db_path=test_db_path)
    append_trace_event(
        TraceEvent(
            event_id="ev-1",
            trace_id="tr-1",
            permit_id="p-1",
            span=SpanId.ORCHESTRATOR,
            type=TraceEventType.PERMIT_RECEIVED,
        ),
        db_path=test_db_path,
    )
    save_department_result(
        "p-1",
        DepartmentName.ZONING,
        DepartmentStatus.APPROVED,
        attempts=1,
        latency_ms=12.5,
        db_path=test_db_path,
    )

    reset_db(test_db_path)

    assert get_permit("p-1", db_path=test_db_path) is None
    assert get_trace_events("p-1", db_path=test_db_path) == []
    assert get_department_results("p-1", db_path=test_db_path) == {}


# =========================================================================
# 2. Permit CRUD & Lifecycle Tests
# =========================================================================

def test_save_and_get_permit_with_pydantic_model(test_db_path: Path, sample_permit: Permit):
    """Save a Permit model and retrieve it as PermitStatusResponse."""
    permit_id = sample_permit.permit_id
    trace_id = "trace-xyz-123"

    save_permit(
        permit_id=permit_id,
        payload=sample_permit,
        state=SagaState.RECEIVED,
        trace_id=trace_id,
        db_path=test_db_path,
    )

    status_resp = get_permit(permit_id, db_path=test_db_path)
    assert status_resp is not None
    assert status_resp.permit_id == permit_id
    assert status_resp.trace_id == trace_id
    assert status_resp.state == SagaState.RECEIVED
    assert status_resp.final_decision == FinalDecision.PENDING
    assert status_resp.decision_code == DecisionCode.PENDING
    assert status_resp.decided_at is None
    assert set(status_resp.department_results.keys()) == {"zoning", "fire", "water", "structural"}
    assert all(d.status == DepartmentStatus.PENDING for d in status_resp.department_results.values())

    # Verify payload extraction helpers
    payload_dict = get_permit_payload(permit_id, db_path=test_db_path)
    assert payload_dict is not None
    assert payload_dict["project_title"] == "Two-Story Single Family Residence"

    raw_payload = get_permit_raw_payload(permit_id, db_path=test_db_path)
    assert raw_payload is not None
    assert "Homer Simpson" in raw_payload


def test_save_permit_with_dict_payload(test_db_path: Path):
    """Save a permit where payload is a standard Python dict."""
    permit_id = "permit-dict-1"
    payload = {"project_title": "Deck Addition", "cost": 15000}

    save_permit(
        permit_id=permit_id,
        payload=payload,
        state="RECEIVED",
        trace_id="tr-dict-1",
        db_path=test_db_path,
    )

    resp = get_permit(permit_id, db_path=test_db_path)
    assert resp is not None
    assert resp.permit_id == permit_id
    assert resp.trace_id == "tr-dict-1"
    assert resp.state == SagaState.RECEIVED


def test_update_permit_state(test_db_path: Path, sample_permit: Permit):
    """Update permit state to EVALUATING."""
    permit_id = "permit-eval-1"
    save_permit(permit_id, sample_permit, state=SagaState.RECEIVED, db_path=test_db_path)

    updated = update_permit_state(permit_id, SagaState.EVALUATING, db_path=test_db_path)
    assert updated is True

    resp = get_permit(permit_id, db_path=test_db_path)
    assert resp is not None
    assert resp.state == SagaState.EVALUATING


def test_update_permit_decision_approved(test_db_path: Path, sample_permit: Permit):
    """Update permit consensus to APPROVED with decision code and summary."""
    permit_id = "permit-decide-appr"
    save_permit(permit_id, sample_permit, state=SagaState.RECEIVED, db_path=test_db_path)

    decided_at = datetime(2026, 11, 14, 15, 30, 0, tzinfo=timezone.utc)
    updated = update_permit_decision(
        permit_id=permit_id,
        state=SagaState.DECIDED,
        final_decision=FinalDecision.APPROVED,
        decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
        decision_summary="All 4 departments reviewed and approved the application.",
        decided_at=decided_at,
        db_path=test_db_path,
    )
    assert updated is True

    resp = get_permit(permit_id, db_path=test_db_path)
    assert resp is not None
    assert resp.state == SagaState.DECIDED
    assert resp.final_decision == FinalDecision.APPROVED
    assert resp.decision_code == DecisionCode.ALL_DEPARTMENTS_APPROVED
    assert resp.decision_summary == "All 4 departments reviewed and approved the application."
    assert resp.decided_at == "2026-11-14T15:30:00Z"


def test_update_permit_decision_rejected(test_db_path: Path, sample_permit: Permit):
    """Update permit consensus to REJECTED with department rejections."""
    permit_id = "permit-decide-rej"
    save_permit(permit_id, sample_permit, state=SagaState.RECEIVED, db_path=test_db_path)

    updated = update_permit_decision(
        permit_id=permit_id,
        state=SagaState.DECIDED,
        final_decision=FinalDecision.REJECTED,
        decision_code=DecisionCode.DEPARTMENT_REJECTIONS,
        decision_summary="Zoning height violation; Fire egress insufficient.",
        db_path=test_db_path,
    )
    assert updated is True

    resp = get_permit(permit_id, db_path=test_db_path)
    assert resp is not None
    assert resp.final_decision == FinalDecision.REJECTED
    assert resp.decision_code == DecisionCode.DEPARTMENT_REJECTIONS
    assert resp.decided_at is not None


def test_get_permit_nonexistent_returns_none(test_db_path: Path):
    """Querying a non-existent permit_id returns None."""
    assert get_permit("does-not-exist", db_path=test_db_path) is None
    assert get_permit_payload("does-not-exist", db_path=test_db_path) is None


# =========================================================================
# 3. Department Results Tests
# =========================================================================

def test_save_and_get_department_results(test_db_path: Path, sample_permit: Permit):
    """Save results across all 4 departments and verify get_department_results."""
    permit_id = "permit-dept-results-1"
    save_permit(permit_id, sample_permit, db_path=test_db_path)

    # 1. Zoning - APPROVED with reasons
    zoning_reasons = [
        EvaluationReason(
            rule_id="ZON-001",
            rule_name="Height Check",
            passed=True,
            severity=RuleSeverity.INFO,
            message="Height within 35ft limit",
        ),
        EvaluationReason(
            rule_id="ZON-002",
            rule_name="Setback Check",
            passed=True,
            severity=RuleSeverity.INFO,
            message="Setbacks comply with R-1",
        ),
    ]
    save_department_result(
        permit_id=permit_id,
        department=DepartmentName.ZONING,
        status=DepartmentStatus.APPROVED,
        attempts=1,
        latency_ms=45.2,
        reasons=zoning_reasons,
        error_message=None,
        db_path=test_db_path,
    )

    # 2. Fire - REJECTED with violation reason
    fire_reasons = [
        EvaluationReason(
            rule_id="FIRE-003",
            rule_name="Hydrant Distance",
            passed=False,
            severity=RuleSeverity.VIOLATION,
            message="Distance to hydrant exceeds maximum 100ft",
        )
    ]
    save_department_result(
        permit_id=permit_id,
        department=DepartmentName.FIRE,
        status=DepartmentStatus.REJECTED,
        attempts=2,
        latency_ms=180.5,
        reasons=fire_reasons,
        error_message=None,
        db_path=test_db_path,
    )

    # 3. Water - TIMEOUT
    save_department_result(
        permit_id=permit_id,
        department=DepartmentName.WATER,
        status=DepartmentStatus.TIMEOUT,
        attempts=3,
        latency_ms=2000.0,
        reasons=[],
        error_message="Department request exceeded 2 seconds",
        db_path=test_db_path,
    )

    # 4. Structural - ERROR
    save_department_result(
        permit_id=permit_id,
        department="structural",
        status="ERROR",
        attempts=3,
        latency_ms=15.0,
        reasons=None,
        error_message="503 Service Unavailable",
        db_path=test_db_path,
    )

    results = get_department_results(permit_id, db_path=test_db_path)
    assert len(results) == 4
    assert set(results.keys()) == {"zoning", "fire", "water", "structural"}

    # Validate Zoning result
    assert results["zoning"].status == DepartmentStatus.APPROVED
    assert results["zoning"].attempts == 1
    assert results["zoning"].latency_ms == 45.2
    assert len(results["zoning"].reasons) == 2
    assert results["zoning"].reasons[0].rule_id == "ZON-001"
    assert results["zoning"].error_message is None

    # Validate Fire result
    assert results["fire"].status == DepartmentStatus.REJECTED
    assert results["fire"].attempts == 2
    assert len(results["fire"].reasons) == 1
    assert results["fire"].reasons[0].passed is False

    # Validate Water result
    assert results["water"].status == DepartmentStatus.TIMEOUT
    assert results["water"].error_message == "Department request exceeded 2 seconds"

    # Validate Structural result
    assert results["structural"].status == DepartmentStatus.ERROR
    assert results["structural"].error_message == "503 Service Unavailable"

    # Verify that get_permit includes these department results
    permit_status = get_permit(permit_id, db_path=test_db_path)
    assert permit_status is not None
    assert len(permit_status.department_results) == 4
    assert permit_status.department_results["zoning"].status == DepartmentStatus.APPROVED


def test_department_result_upsert(test_db_path: Path):
    """Updating a department result replaces the old result for that (permit_id, department)."""
    permit_id = "permit-upsert-1"
    save_permit(permit_id, "{}", db_path=test_db_path)

    # Initial attempt: TIMEOUT
    save_department_result(
        permit_id=permit_id,
        department=DepartmentName.FIRE,
        status=DepartmentStatus.TIMEOUT,
        attempts=1,
        latency_ms=2000.0,
        error_message="Timed out attempt 1",
        db_path=test_db_path,
    )
    first_res = get_department_results(permit_id, db_path=test_db_path)
    assert first_res["fire"].status == DepartmentStatus.TIMEOUT
    assert first_res["fire"].attempts == 1

    # Retry attempt: APPROVED
    save_department_result(
        permit_id=permit_id,
        department=DepartmentName.FIRE,
        status=DepartmentStatus.APPROVED,
        attempts=2,
        latency_ms=120.0,
        reasons=[
            EvaluationReason(
                rule_id="F-1",
                rule_name="Sprinkler OK",
                passed=True,
                severity=RuleSeverity.INFO,
                message="Sprinklers verified",
            )
        ],
        error_message=None,
        db_path=test_db_path,
    )
    second_res = get_department_results(permit_id, db_path=test_db_path)
    assert second_res["fire"].status == DepartmentStatus.APPROVED
    assert second_res["fire"].attempts == 2
    assert second_res["fire"].error_message is None
    assert len(second_res["fire"].reasons) == 1


# =========================================================================
# 4. Append-Only Trace Events Tests
# =========================================================================

def test_append_and_query_trace_events_order(test_db_path: Path):
    """Verify trace events are retrieved in exact chronological append-only order."""
    permit_id = "permit-trace-1"
    trace_id = "trace-stream-100"

    events = [
        TraceEvent(
            event_id=f"ev-{i}",
            trace_id=trace_id,
            permit_id=permit_id,
            span=SpanId.ORCHESTRATOR if i in (0, 4) else SpanId.DEPT_ZONING,
            parent_span=None if i == 0 else "orchestrator",
            type=[
                TraceEventType.PERMIT_RECEIVED,
                TraceEventType.CALL_DISPATCHED,
                TraceEventType.CALL_SUCCEEDED,
                TraceEventType.SPAN_COMPLETED,
                TraceEventType.PERMIT_DECIDED,
            ][i],
            ts=f"2026-11-14T10:00:0{i}Z",
            detail={"step": i, "desc": f"Event {i}"},
        )
        for i in range(5)
    ]

    for ev in events:
        append_trace_event(ev, db_path=test_db_path)

    retrieved = get_trace_events(permit_id, db_path=test_db_path)
    assert len(retrieved) == 5

    for i in range(5):
        assert retrieved[i].event_id == f"ev-{i}"
        assert retrieved[i].trace_id == trace_id
        assert retrieved[i].permit_id == permit_id
        assert retrieved[i].detail["step"] == i
        assert retrieved[i].ts == f"2026-11-14T10:00:0{i}Z"


def test_batch_append_trace_events(test_db_path: Path):
    """Verify append_trace_events batch writes all events correctly."""
    permit_id = "permit-batch-1"
    trace_id = "trace-batch-1"

    batch = [
        TraceEvent(
            event_id=f"b-ev-{i}",
            trace_id=trace_id,
            permit_id=permit_id,
            span=SpanId.CONSENSUS,
            type=TraceEventType.CONSENSUS_STARTED,
            detail={"index": i},
        )
        for i in range(10)
    ]

    append_trace_events(batch, db_path=test_db_path)

    retrieved = get_trace_events(permit_id, db_path=test_db_path)
    assert len(retrieved) == 10
    assert [e.detail["index"] for e in retrieved] == list(range(10))


def test_trace_events_isolated_by_permit(test_db_path: Path):
    """Events for permit A do not leak into queries for permit B."""
    append_trace_event(
        TraceEvent(
            event_id="ev-A",
            trace_id="tr-A",
            permit_id="permit-A",
            span=SpanId.ORCHESTRATOR,
            type=TraceEventType.PERMIT_RECEIVED,
        ),
        db_path=test_db_path,
    )
    append_trace_event(
        TraceEvent(
            event_id="ev-B",
            trace_id="tr-B",
            permit_id="permit-B",
            span=SpanId.ORCHESTRATOR,
            type=TraceEventType.PERMIT_RECEIVED,
        ),
        db_path=test_db_path,
    )

    events_a = get_trace_events("permit-A", db_path=test_db_path)
    events_b = get_trace_events("permit-B", db_path=test_db_path)

    assert len(events_a) == 1
    assert events_a[0].event_id == "ev-A"

    assert len(events_b) == 1
    assert events_b[0].event_id == "ev-B"


# =========================================================================
# 5. Thread Safety & High Concurrency Tests
# =========================================================================

def test_concurrent_multithreaded_writes_and_reads(test_db_path: Path, sample_permit: Permit):
    """
    Simulate high-concurrency fan-out saga writes across multiple threads.
    Asserts no 'database locked' errors and data integrity in SQLite WAL mode.
    """
    num_permits = 15
    num_events_per_permit = 8
    threads = 10

    def worker(worker_id: int):
        for i in range(num_permits):
            permit_id = f"permit-concurrent-{worker_id}-{i}"
            trace_id = f"trace-concurrent-{worker_id}-{i}"

            # Save initial permit
            save_permit(
                permit_id=permit_id,
                payload=sample_permit,
                state=SagaState.RECEIVED,
                trace_id=trace_id,
                db_path=test_db_path,
            )

            # Emit initial event
            append_trace_event(
                TraceEvent(
                    event_id=f"ev-{permit_id}-root",
                    trace_id=trace_id,
                    permit_id=permit_id,
                    span=SpanId.ORCHESTRATOR,
                    type=TraceEventType.PERMIT_RECEIVED,
                ),
                db_path=test_db_path,
            )

            # Update state to evaluating
            update_permit_state(permit_id, SagaState.EVALUATING, db_path=test_db_path)

            # Simulate department results writes
            for dept in [DepartmentName.ZONING, DepartmentName.FIRE, DepartmentName.WATER, DepartmentName.STRUCTURAL]:
                save_department_result(
                    permit_id=permit_id,
                    department=dept,
                    status=DepartmentStatus.APPROVED,
                    attempts=1,
                    latency_ms=25.0,
                    reasons=[],
                    db_path=test_db_path,
                )

            # Concurrent append of multiple trace events
            for step in range(num_events_per_permit):
                append_trace_event(
                    TraceEvent(
                        event_id=f"ev-{permit_id}-step-{step}",
                        trace_id=trace_id,
                        permit_id=permit_id,
                        span=SpanId.CONSENSUS,
                        type=TraceEventType.CALL_SUCCEEDED,
                        detail={"worker": worker_id, "step": step},
                    ),
                    db_path=test_db_path,
                )

            # Update final decision
            update_permit_decision(
                permit_id=permit_id,
                state=SagaState.DECIDED,
                final_decision=FinalDecision.APPROVED,
                decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
                decision_summary="Concurrency test approved",
                db_path=test_db_path,
            )

            # Verify read immediately
            status = get_permit(permit_id, db_path=test_db_path)
            assert status is not None
            assert status.state == SagaState.DECIDED
            assert len(status.department_results) == 4

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(worker, w_id) for w_id in range(4)]
        for f in futures:
            f.result()  # Will raise if any thread encountered an exception


# =========================================================================
# 6. Edge Cases & Special Characters
# =========================================================================

def test_special_characters_and_unicode_handling(test_db_path: Path):
    """Test unicode characters, emojis, quotes, and newlines in payloads, reasons, and details."""
    permit_id = "permit-unicode-🚀"
    special_payload = {
        "title": "Café & Crêperie \"L'Étoile\" \n Line 2 \t Tabbed",
        "description": "Unicode test with 🏗️, 🏢, and 🚒 emojis",
        "nested": {"quotes": 'Single \' and double " quotes', "brackets": "[{()}]"},
    }

    save_permit(permit_id, special_payload, db_path=test_db_path)

    reasons = [
        EvaluationReason(
            rule_id="RULE-ÜNÎCØDÉ",
            rule_name="Accent & emoji check 🌐",
            passed=True,
            severity=RuleSeverity.INFO,
            message="Check passed: \"Valid café roofline\" & 100% compliant.",
        )
    ]
    save_department_result(
        permit_id=permit_id,
        department=DepartmentName.ZONING,
        status=DepartmentStatus.APPROVED,
        attempts=1,
        latency_ms=10.0,
        reasons=reasons,
        db_path=test_db_path,
    )

    append_trace_event(
        TraceEvent(
            event_id="ev-unicode-1",
            trace_id="tr-unicode-1",
            permit_id=permit_id,
            span=SpanId.DEPT_ZONING,
            type=TraceEventType.CALL_SUCCEEDED,
            detail={"message": "Café approved! ☕🎉"},
        ),
        db_path=test_db_path,
    )

    # Read back and verify
    permit = get_permit(permit_id, db_path=test_db_path)
    assert permit is not None
    assert permit.permit_id == permit_id
    dept_res = permit.department_results["zoning"]
    assert dept_res.reasons[0].rule_id == "RULE-ÜNÎCØDÉ"
    assert "café" in dept_res.reasons[0].message

    events = get_trace_events(permit_id, db_path=test_db_path)
    assert len(events) == 1
    assert events[0].detail["message"] == "Café approved! ☕🎉"


def test_get_permit_schema_conformance_for_inflight_and_decided_permits(
    test_db_path: Path, sample_permit: Permit
):
    """Verify that get_permit returns objects fully conforming to permit_status_response.schema.json."""
    from jsonschema import Draft7Validator, FormatChecker
    from referencing import Registry, Resource

    schemas_dir = Path("contract/schemas")
    resources = []
    for p in schemas_dir.glob("*.json"):
        s = json.loads(p.read_text())
        resources.append((p.name, Resource.from_contents(s)))
    registry = Registry().with_resources(resources)
    schema = json.loads((schemas_dir / "permit_status_response.schema.json").read_text())
    validator = Draft7Validator(schema, registry=registry, format_checker=FormatChecker())

    permit_id = "p-schema-test-1"
    # 1. In-flight permit (state: RECEIVED, no department results written yet)
    save_permit(
        permit_id=permit_id,
        payload=sample_permit,
        state=SagaState.RECEIVED,
        trace_id="tr-schema-1",
        db_path=test_db_path,
    )
    inflight = get_permit(permit_id, db_path=test_db_path)
    assert inflight is not None
    data_inflight = inflight.model_dump(mode="json")
    errors = list(validator.iter_errors(data_inflight))
    assert not errors, f"In-flight permit schema validation failed: {errors}"

    # 2. Decided permit with actual department results
    save_department_result(
        permit_id=permit_id,
        department=DepartmentName.ZONING,
        status=DepartmentStatus.APPROVED,
        attempts=1,
        latency_ms=12.5,
        reasons=[
            EvaluationReason(
                rule_id="ZON-1",
                rule_name="Height check",
                passed=True,
                message="Height within limit",
                severity=RuleSeverity.INFO,
            )
        ],
        db_path=test_db_path,
    )
    update_permit_decision(
        permit_id=permit_id,
        state=SagaState.DECIDED,
        final_decision=FinalDecision.APPROVED,
        decision_code=DecisionCode.ALL_DEPARTMENTS_APPROVED,
        decision_summary="All departments approved",
        db_path=test_db_path,
    )
    decided = get_permit(permit_id, db_path=test_db_path)
    assert decided is not None
    data_decided = decided.model_dump(mode="json")
    errors_decided = list(validator.iter_errors(data_decided))
    assert not errors_decided, f"Decided permit schema validation failed: {errors_decided}"

