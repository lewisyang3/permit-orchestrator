"""
Contract Models Verification Test Suite.
Validates Pydantic v2 models in contract/models.py against:
1. All fixtures in contract/fixtures/
2. JSON schemas in contract/schemas/
3. Runtime serialization, datetime RFC 3339 formatting, and enum safety
4. Missing endpoint models (SubmitPermitResponse, StreamDonePayload, AdminFaultResponse)
"""

import json
from pathlib import Path
from datetime import datetime, timezone
import pytest
import jsonschema
from referencing import Registry, Resource
from jsonschema import Draft7Validator, FormatChecker

import contract.models as cm

SCHEMAS_DIR = Path("contract/schemas")
FIXTURES_DIR = Path("contract/fixtures")


def _build_registry() -> Registry:
    resources = []
    for p in SCHEMAS_DIR.glob("*.json"):
        schema = json.loads(p.read_text())
        resources.append((p.name, Resource.from_contents(schema)))
    return Registry().with_resources(resources)


REGISTRY = _build_registry()


def get_validator(schema_name: str) -> Draft7Validator:
    schema = json.loads((SCHEMAS_DIR / schema_name).read_text())
    return Draft7Validator(schema, registry=REGISTRY, format_checker=FormatChecker())


def test_fixtures_permit_parse_and_serialize():
    """Validate all permit fixtures deserialize to Permit and serialize cleanly."""
    permit_files = [
        "permit_approved.json",
        "permit_rejected_fire.json",
        "permit_timeout_offline.json",
    ]
    validator = get_validator("permit.schema.json")

    for filename in permit_files:
        raw_data = json.loads((FIXTURES_DIR / filename).read_text())
        permit = cm.Permit.model_validate(raw_data)
        assert permit.permit_id == raw_data["permit_id"]
        assert isinstance(permit.applicant.role, cm.ApplicantRole)
        assert isinstance(permit.structural.seismic_design_category, cm.SeismicDesignCategory)

        dumped = json.loads(permit.model_dump_json())
        errors = list(validator.iter_errors(dumped))
        assert not errors, f"Permit {filename} schema errors: {errors}"


def test_fixtures_trace_events_parse_and_serialize():
    """Validate all trace event stream fixtures deserialize to TraceEvent and serialize cleanly."""
    trace_files = [
        "trace_events_approved.json",
        "trace_events_rejected.json",
        "trace_events_timeout.json",
    ]
    validator = get_validator("trace_event.schema.json")

    for filename in trace_files:
        events_data = json.loads((FIXTURES_DIR / filename).read_text())
        assert len(events_data) > 0

        for raw_event in events_data:
            te = cm.TraceEvent.model_validate(raw_event)
            assert te.event_id == raw_event["event_id"]
            assert te.trace_id == raw_event["trace_id"]
            assert isinstance(te.span, cm.SpanId)

            dumped = json.loads(te.model_dump_json())
            errors = list(validator.iter_errors(dumped))
            assert not errors, f"TraceEvent {te.event_id} in {filename} schema errors: {errors}"


def test_permit_intake_without_id():
    """Test that client can instantiate a Permit without an ID on initial intake submission."""
    raw_permit = json.loads((FIXTURES_DIR / "permit_approved.json").read_text())
    raw_permit["permit_id"] = None
    permit = cm.Permit.model_validate(raw_permit)
    assert permit.permit_id is None
    dumped = json.loads(permit.model_dump_json())
    assert dumped["permit_id"] is None


def test_department_evaluation_request():
    """Validate DepartmentEvaluationRequest creation and serialization."""
    raw_permit = json.loads((FIXTURES_DIR / "permit_approved.json").read_text())
    permit = cm.Permit.model_validate(raw_permit)

    req = cm.DepartmentEvaluationRequest(
        permit_id="pm-test-001",
        trace_id="trc-test-123",
        department=cm.DepartmentName.ZONING,
        attempt=1,
        permit=permit,
    )
    dumped = json.loads(req.model_dump_json())
    validator = get_validator("department_request.schema.json")
    errors = list(validator.iter_errors(dumped))
    assert not errors, f"DepartmentEvaluationRequest schema validation failed: {errors}"


def test_department_evaluation_response_timezone_aware():
    """Test DepartmentEvaluationResponse default evaluated_at serializes to RFC 3339 with Z."""
    resp = cm.DepartmentEvaluationResponse(
        permit_id="pm-test-001",
        department=cm.DepartmentName.FIRE,
        status=cm.DepartmentStatus.APPROVED,
        execution_time_ms=18.5,
        reasons=[
            cm.EvaluationReason(
                rule_id="FIRE-001",
                rule_name="Sprinkler Check",
                passed=True,
                severity=cm.RuleSeverity.INFO,
                message="Sprinkler requirement met or not applicable",
            )
        ],
    )
    dumped = json.loads(resp.model_dump_json())
    # Assert timestamp ends with Z and passes RFC 3339 format validation
    assert dumped["evaluated_at"].endswith("Z")
    validator = get_validator("department_response.schema.json")
    errors = list(validator.iter_errors(dumped))
    assert not errors, f"DepartmentEvaluationResponse schema errors: {errors}"


def test_trace_event_with_datetime():
    """Test TraceEvent handles both datetime objects and string timestamps, always emitting valid ISO strings."""
    # Instantiated with datetime object
    now_utc = datetime.now(timezone.utc)
    te_dt = cm.TraceEvent(
        event_id="evt-001",
        trace_id="trc-001",
        permit_id="pm-001",
        span=cm.SpanId.ORCHESTRATOR,
        parent_span=None,
        type=cm.TraceEventType.PERMIT_RECEIVED,
        ts=now_utc,
        detail={"info": "started"},
    )
    dumped_dt = json.loads(te_dt.model_dump_json())
    assert dumped_dt["ts"].endswith("Z")

    validator = get_validator("trace_event.schema.json")
    errors = list(validator.iter_errors(dumped_dt))
    assert not errors, f"TraceEvent with datetime failed schema validation: {errors}"

    # Instantiated with string timestamp
    te_str = cm.TraceEvent(
        event_id="evt-002",
        trace_id="trc-001",
        permit_id="pm-001",
        span=cm.SpanId.DEPT_ZONING,
        parent_span="orchestrator",
        type=cm.TraceEventType.SPAN_STARTED,
        ts="2026-11-13T16:34:40.020Z",
        detail={},
    )
    dumped_str = json.loads(te_str.model_dump_json())
    assert dumped_str["ts"] == "2026-11-13T16:34:40.020Z"
    errors = list(validator.iter_errors(dumped_str))
    assert not errors, f"TraceEvent with str timestamp failed schema validation: {errors}"


def test_permit_status_response_in_flight():
    """Verify that in-flight permit status payloads without decisions deserialize and serialize cleanly."""
    in_flight_payload = {
        "permit_id": "pm-001",
        "trace_id": "trc-001",
        "state": "EVALUATING",
        "final_decision": "PENDING",
        "created_at": "2026-11-13T16:00:00Z",
        "department_results": {
            "zoning": {"status": "PENDING", "attempts": 0, "latency_ms": 0.0, "reasons": []},
            "fire": {"status": "PENDING", "attempts": 0, "latency_ms": 0.0, "reasons": []},
            "water": {"status": "PENDING", "attempts": 0, "latency_ms": 0.0, "reasons": []},
            "structural": {"status": "PENDING", "attempts": 0, "latency_ms": 0.0, "reasons": []},
        },
    }
    psr = cm.PermitStatusResponse.model_validate(in_flight_payload)
    assert psr.state == cm.SagaState.EVALUATING
    assert psr.final_decision == cm.FinalDecision.PENDING
    assert psr.decision_code == cm.DecisionCode.PENDING
    assert psr.decision_summary is None
    assert psr.decided_at is None

    dumped = json.loads(psr.model_dump_json())
    assert dumped["decision_code"] == "PENDING"
    assert dumped["decision_summary"] is None


def test_permit_status_response_completed_with_datetime():
    """Verify that completed PermitStatusResponse with datetime objects serializes cleanly to schema."""
    psr = cm.PermitStatusResponse(
        permit_id="pm-fixture-approved-001",
        trace_id="trc-app-001",
        state=cm.SagaState.DECIDED,
        final_decision=cm.FinalDecision.APPROVED,
        decision_code=cm.DecisionCode.ALL_DEPARTMENTS_APPROVED,
        decision_summary="All 4 departments approved.",
        created_at=datetime.now(timezone.utc),
        decided_at=datetime.now(timezone.utc),
        department_results={
            "zoning": cm.DepartmentResultSummary(status=cm.DepartmentStatus.APPROVED, attempts=1, latency_ms=45.0),
            "fire": cm.DepartmentResultSummary(status=cm.DepartmentStatus.APPROVED, attempts=1, latency_ms=50.0),
            "water": cm.DepartmentResultSummary(status=cm.DepartmentStatus.APPROVED, attempts=1, latency_ms=30.0),
            "structural": cm.DepartmentResultSummary(status=cm.DepartmentStatus.APPROVED, attempts=1, latency_ms=40.0),
        },
    )
    dumped = json.loads(psr.model_dump_json())
    assert dumped["created_at"].endswith("Z")
    assert dumped["decided_at"].endswith("Z")

    validator = get_validator("permit_status_response.schema.json")
    errors = list(validator.iter_errors(dumped))
    assert not errors, f"PermitStatusResponse schema errors: {errors}"


def test_fault_control():
    """Test FaultControlRequest and FaultControlResponse models."""
    req = cm.FaultControlRequest(
        department=cm.DepartmentName.FIRE,
        mode=cm.FaultMode.SLOW,
        delay_ms=3000,
    )
    assert req.mode == cm.FaultMode.SLOW
    assert req.department == cm.DepartmentName.FIRE

    resp = cm.FaultControlResponse(
        department=cm.DepartmentName.FIRE,
        mode=cm.FaultMode.SLOW,
        delay_ms=3000,
        message="Simulated slow response enabled",
    )
    assert resp.mode == cm.FaultMode.SLOW


def test_new_endpoint_contracts():
    """Test SubmitPermitResponse, StreamDonePayload, and AdminFaultResponse with their JSON schemas."""
    # 1. SubmitPermitResponse
    sub_resp = cm.SubmitPermitResponse(
        permit_id="pm-new-001",
        trace_id="trc-new-001",
        state=cm.SagaState.RECEIVED,
        stream_url="/permits/pm-new-001/stream",
    )
    assert sub_resp.state == cm.SagaState.RECEIVED
    dumped_sub = json.loads(sub_resp.model_dump_json())
    assert dumped_sub["stream_url"] == "/permits/pm-new-001/stream"
    v_sub = get_validator("submit_permit_response.schema.json")
    assert not list(v_sub.iter_errors(dumped_sub))

    # 2. StreamDonePayload
    done_ev = cm.StreamDonePayload(
        permit_id="pm-new-001",
        final_decision=cm.FinalDecision.APPROVED,
    )
    assert done_ev.final_decision == cm.FinalDecision.APPROVED
    assert cm.StreamDoneEvent is cm.StreamDonePayload
    dumped_done = json.loads(done_ev.model_dump_json())
    v_done = get_validator("sse_done_event.schema.json")
    assert not list(v_done.iter_errors(dumped_done))

    # 3. AdminFaultResponse
    admin_resp = cm.AdminFaultResponse(
        zoning=cm.FaultControlResponse(department=cm.DepartmentName.ZONING, mode=cm.FaultMode.NONE, delay_ms=0, message="Normal operation"),
        fire=cm.FaultControlResponse(department=cm.DepartmentName.FIRE, mode=cm.FaultMode.OFFLINE, delay_ms=3000, message="Fire service offline"),
        water=cm.FaultControlResponse(department=cm.DepartmentName.WATER, mode=cm.FaultMode.NONE, delay_ms=0, message="Normal operation"),
        structural=cm.FaultControlResponse(department=cm.DepartmentName.STRUCTURAL, mode=cm.FaultMode.NONE, delay_ms=0, message="Normal operation"),
    )
    assert admin_resp.fire.mode == cm.FaultMode.OFFLINE
    dumped_admin = json.loads(admin_resp.model_dump_json())
    v_admin = get_validator("admin_fault_response.schema.json")
    assert not list(v_admin.iter_errors(dumped_admin))


def test_nullable_optional_fields_schema_conformance():
    """Verify that models with null optional fields conform to updated nullable JSON schemas."""
    # 1. FaultControlRequest with null department
    fc = cm.FaultControlRequest(mode=cm.FaultMode.NONE)
    dumped_fc = json.loads(fc.model_dump_json())
    assert dumped_fc["department"] is None
    v_fc = get_validator("fault_control.schema.json")
    assert not list(v_fc.iter_errors(dumped_fc))

    # 2. Permit with null project_description, applicant.phone, and omitted permit_id on intake
    raw_permit = json.loads((FIXTURES_DIR / "permit_approved.json").read_text())
    del raw_permit["permit_id"]
    raw_permit["project_description"] = None
    raw_permit["applicant"]["phone"] = None
    permit = cm.Permit.model_validate(raw_permit)
    dumped_permit = json.loads(permit.model_dump_json(exclude_none=True))
    v_permit = get_validator("permit.schema.json")
    assert not list(v_permit.iter_errors(dumped_permit))

    # 3. PermitStatusResponse with null decision_summary and decided_at
    psr = cm.PermitStatusResponse(
        permit_id="pm-001",
        trace_id="trc-001",
        state=cm.SagaState.EVALUATING,
        final_decision=cm.FinalDecision.PENDING,
        decision_code=cm.DecisionCode.PENDING,
        decision_summary=None,
        created_at="2026-11-13T16:00:00Z",
        decided_at=None,
        department_results={
            "zoning": cm.DepartmentResultSummary(status=cm.DepartmentStatus.PENDING, attempts=0, latency_ms=0.0),
            "fire": cm.DepartmentResultSummary(status=cm.DepartmentStatus.PENDING, attempts=0, latency_ms=0.0),
            "water": cm.DepartmentResultSummary(status=cm.DepartmentStatus.PENDING, attempts=0, latency_ms=0.0),
            "structural": cm.DepartmentResultSummary(status=cm.DepartmentStatus.PENDING, attempts=0, latency_ms=0.0),
        },
    )
    dumped_psr = json.loads(psr.model_dump_json())
    v_psr = get_validator("permit_status_response.schema.json")
    assert not list(v_psr.iter_errors(dumped_psr))

