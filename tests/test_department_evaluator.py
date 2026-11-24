"""Comprehensive tests for department evaluation service, deterministic rules, and Gemini integration.

Verifies:
1. All 4 departments (zoning, fire, water, structural) and mismatched department rejection (HTTP 422).
2. Numeric boundaries (exact threshold, just below, just above).
3. Conditional branches (cases selection based on when conditions).
4. Calculations and independent checks (e.g. emergency escape width/height vs net clear area).
5. Missing facts (treated as unknown, not zero/false -> NEEDS_INFORMATION).
6. Invalid fact types (booleans-as-numbers rejected, non-finite NaN/Inf rejected, zero preserved as valid measurement).
7. Unknown applicability and unresolved exceptions/prerequisites -> NEEDS_INFORMATION / REQUIRES_HUMAN_REVIEW.
8. Empty object inventories cannot pass.
9. Malformed YAML rule pack at startup raises ValueError.
10. Partial-pack approval restrictions: passing all checks still cannot yield APPROVED.
11. Gemini integration: mock tests for success, timeout, provider failure, malformed JSON, and missing API key.
    Verify fallback message used and decision unchanged. Zero live billable calls during tests.
12. Orchestrator integration: test fanout with dev/orchestrator.py harness.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from contract.models import (
    Applicant,
    ApplicantRole,
    DepartmentEvaluationRequest,
    DepartmentName,
    DepartmentStatus,
    FireData,
    OccupancyType,
    Permit,
    PropertyAddress,
    RuleSeverity,
    StructuralData,
    WaterData,
    ZoneCode,
    ZoningData,
)
from departments.app import create_app as department_app
from departments.evaluator import (
    EvaluationOutcome,
    RuleEvaluationResult,
    evaluate_condition,
    evaluate_department,
    evaluate_rule,
    get_mapped_and_missing_facts,
    load_and_validate_rule_pack,
    map_permit_to_facts,
    perform_explicit_calculations,
    validate_numeric_measurement,
)
from dev.orchestrator import create_app as orchestrator_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_permit_dict() -> Dict[str, Any]:
    return json.loads(Path("contract/fixtures/permit_approved.json").read_text())


@pytest.fixture
def base_permit(base_permit_dict: Dict[str, Any]) -> Permit:
    return Permit.model_validate(base_permit_dict)


@pytest.fixture
def zoning_pack() -> Dict[str, Any]:
    return load_and_validate_rule_pack(Path("rules/ca-san-jose/zoning.yaml"), DepartmentName.ZONING)


@pytest.fixture
def fire_pack() -> Dict[str, Any]:
    return load_and_validate_rule_pack(Path("rules/ca-san-jose/fire.yaml"), DepartmentName.FIRE)


@pytest.fixture
def water_pack() -> Dict[str, Any]:
    return load_and_validate_rule_pack(Path("rules/ca-san-jose/water.yaml"), DepartmentName.WATER)


@pytest.fixture
def structural_pack() -> Dict[str, Any]:
    return load_and_validate_rule_pack(Path("rules/ca-san-jose/structural.yaml"), DepartmentName.STRUCTURAL)


# ---------------------------------------------------------------------------
# 1. All 4 Departments and Mismatched Department Rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "dept_name,expected_count",
    [
        (DepartmentName.ZONING, 5),
        (DepartmentName.FIRE, 6),
        (DepartmentName.WATER, 7),
        (DepartmentName.STRUCTURAL, 6),
    ],
)
def test_all_four_rule_packs_load_and_validate_successfully(dept_name, expected_count):
    pack = load_and_validate_rule_pack(f"rules/ca-san-jose/{dept_name.value}.yaml", expected_department=dept_name)
    assert pack["department"] == dept_name.value
    assert len(pack["rules"]) == expected_count
    assert pack["evaluation_policy"]["allow_final_department_approval_from_this_pack_alone"] is False


def test_all_yaml_embedded_examples_match_expected_outcomes():
    """Verify all 50 official starter rule examples across all 4 YAML packs evaluate to their expected outcomes."""
    import glob
    total_examples = 0
    for path in sorted(p for p in glob.glob("rules/*/*.yaml") if not p.endswith("city.yaml")):
        pack = load_and_validate_rule_pack(path)
        for rule in pack.get("rules", []):
            for ex in rule.get("examples", []):
                facts = ex.get("facts", {})
                expected = ex.get("expected_outcome")
                res = evaluate_rule(rule, facts)
                assert res.outcome.value == expected, f"{rule['rule_id']} expected {expected}, got {res.outcome.value}"
                total_examples += 1
    assert total_examples == 50


@pytest.mark.parametrize(
    "dept_str,count",
    [
        ("zoning", 5),
        ("fire", 6),
        ("water", 7),
        ("structural", 6),
    ],
)
def test_all_four_department_apps_health_and_rules(dept_str, count):
    with TestClient(department_app(dept_str)) as client:
        health_resp = client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "healthy", "department": dept_str}

        rules_resp = client.get("/rules")
        assert rules_resp.status_code == 200
        data = rules_resp.json()
        assert data["department"] == dept_str
        [sj] = data["jurisdictions"]
        assert sj["jurisdiction_id"] == "ca-san-jose"
        assert sj["rule_count"] == count
        assert data["evaluation_ready"] is True


@pytest.mark.parametrize(
    "target_dept,wrong_dept",
    [
        ("fire", "water"),
        ("zoning", "structural"),
        ("water", "zoning"),
        ("structural", "fire"),
    ],
)
def test_mismatched_department_request_rejected_with_422(base_permit_dict, target_dept, wrong_dept):
    with TestClient(department_app(target_dept)) as client:
        response = client.post(
            "/evaluate",
            json={
                "permit_id": "test-pm-01",
                "trace_id": "trace-01",
                "department": wrong_dept,
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 422
        assert "does not match" in response.text


@pytest.mark.parametrize(
    "target_dept,wrong_rule_file",
    [
        ("fire", Path("rules/ca-san-jose/water.yaml")),
        ("zoning", Path("rules/ca-san-jose/fire.yaml")),
        ("water", Path("rules/ca-san-jose/structural.yaml")),
        ("structural", Path("rules/ca-san-jose/zoning.yaml")),
    ],
)
def test_mismatched_rule_pack_file_on_startup_raises_value_error(target_dept, wrong_rule_file):
    with pytest.raises(ValueError, match="does not match expected department"):
        with TestClient(department_app(target_dept, rule_file=wrong_rule_file)):
            pass


def test_department_evaluates_violation_as_200_rejected(tmp_path, base_permit_dict):
    # Create a rule pack with a check that fails against base_permit_dict (hydrant distance 180 ft > 100 ft max)
    violation_pack = {
        "schema_version": "0.2.0",
        "rule_set_id": "test-fire-violation",
        "version": "0.2.0",
        "department": "fire",
        "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": False},
        "rules": [
            {
                "rule_id": "TEST-F-001",
                "rule_name": "Hydrant distance limit",
                "applies_when": [],
                "requirement": {
                    "cases": [
                        {
                            "when": [],
                            "checks": [
                                {
                                    "field": "distance_to_hydrant_ft",
                                    "operator": "lte",
                                    "value": 100.0,
                                    "unit": "ft",
                                }
                            ],
                        }
                    ]
                },
            }
        ],
    }
    rule_file = tmp_path / "fire_violation.yaml"
    import yaml
    rule_file.write_text(yaml.dump(violation_pack))

    with TestClient(department_app("fire", rule_file=rule_file)) as client:
        response = client.post(
            "/evaluate",
            json={
                "permit_id": "bad-pm-01",
                "trace_id": "trace-01",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "REJECTED"
        assert data["department"] == "fire"
        assert len(data["reasons"]) > 0
        rule_ids = [r["rule_id"] for r in data["reasons"]]
        assert "TEST-F-001" in rule_ids
        for r in data["reasons"]:
            if r["rule_id"] == "TEST-F-001":
                assert r["passed"] is False
                assert r["severity"] == "VIOLATION"


def test_department_evaluates_approval_as_200_when_policy_allows(tmp_path, base_permit_dict):
    # Rule pack with allow_final_department_approval_from_this_pack_alone: True and passing checks
    approval_pack = {
        "schema_version": "0.2.0",
        "rule_set_id": "test-fire-approval",
        "version": "0.2.0",
        "department": "fire",
        "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": True},
        "rules": [
            {
                "rule_id": "TEST-F-PASS",
                "rule_name": "Hydrant distance limit",
                "applies_when": [],
                "requirement": {
                    "cases": [
                        {
                            "when": [],
                            "checks": [
                                {
                                    "field": "distance_to_hydrant_ft",
                                    "operator": "lte",
                                    "value": 250.0,
                                    "unit": "ft",
                                }
                            ],
                        }
                    ]
                },
            }
        ],
    }
    rule_file = tmp_path / "fire_approval.yaml"
    import yaml
    rule_file.write_text(yaml.dump(approval_pack))

    with TestClient(department_app("fire", rule_file=rule_file)) as client:
        response = client.post(
            "/evaluate",
            json={
                "permit_id": "good-pm-01",
                "trace_id": "trace-01",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "APPROVED"
        assert data["department"] == "fire"
        assert len(data["reasons"]) == 1
        assert data["reasons"][0]["passed"] is True


def test_department_evaluates_incomplete_as_422_with_code(base_permit_dict):
    with TestClient(department_app("fire")) as client:
        response = client.post(
            "/evaluate",
            json={
                "permit_id": "pm-incomplete",
                "trace_id": "trace-incomplete",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["detail"]["code"] == "INCOMPLETE_REVIEW"
        assert data["detail"]["department"] == "fire"
        assert "unresolved_reasons" in data["detail"]


# ---------------------------------------------------------------------------
# 2. Numeric Boundaries (exact threshold, just below, just above)
# ---------------------------------------------------------------------------

def test_numeric_boundary_gte_exact_below_above(structural_pack):
    # SJ-S-101: bolt_embedment_in >= 7 in
    rule = next(r for r in structural_pack["rules"] if r["rule_id"] == "SJ-S-101")
    base_facts = {
        "prescriptive_crc_anchor_bolt_path": True,
        "engineered_alternative_anchorage": False,
        "bolt_diameter_in": 0.5,
    }

    # Exact threshold -> PASS
    res_exact = evaluate_rule(rule, {**base_facts, "bolt_embedment_in": 7.0})
    assert res_exact.outcome == EvaluationOutcome.PASS
    assert res_exact.passed is True

    # Just below -> FAIL
    res_below = evaluate_rule(rule, {**base_facts, "bolt_embedment_in": 6.99})
    assert res_below.outcome == EvaluationOutcome.FAIL
    assert res_below.passed is False

    # Just above -> PASS
    res_above = evaluate_rule(rule, {**base_facts, "bolt_embedment_in": 7.01})
    assert res_above.outcome == EvaluationOutcome.PASS
    assert res_above.passed is True


def test_numeric_boundary_footing_width_gte(structural_pack):
    # SJ-S-106: footing_width_in >= 12 in, footing_thickness_in >= 6 in, footing_projection_in >= 2 in
    rule = next(r for r in structural_pack["rules"] if r["rule_id"] == "SJ-S-106")
    base_facts = {
        "conventional_t_footing": True,
        "engineered_footing_design": False,
        "footing_thickness_in": 6.0,
        "footing_projection_in": 2.0,
    }

    # Exact width (12.0) -> PASS
    assert evaluate_rule(rule, {**base_facts, "footing_width_in": 12.0}).outcome == EvaluationOutcome.PASS
    # Just below width (11.99) -> FAIL
    assert evaluate_rule(rule, {**base_facts, "footing_width_in": 11.99}).outcome == EvaluationOutcome.FAIL
    # Just above width (12.01) -> PASS
    assert evaluate_rule(rule, {**base_facts, "footing_width_in": 12.01}).outcome == EvaluationOutcome.PASS


def test_numeric_boundary_lte_exact_below_above(zoning_pack):
    # SJ-Z-105: building_height_ft <= 35 ft, stories_count <= 2.5
    rule = next(r for r in zoning_pack["rules"] if r["rule_id"] == "SJ-Z-105")
    base_facts = {"zone_code": "R-1-8", "stories_count": 2.0}

    # Exact threshold (35.0) -> PASS
    res_exact = evaluate_rule(rule, {**base_facts, "building_height_ft": 35.0})
    assert res_exact.outcome == EvaluationOutcome.PASS
    assert res_exact.passed is True

    # Just below (34.99) -> PASS
    res_below = evaluate_rule(rule, {**base_facts, "building_height_ft": 34.99})
    assert res_below.outcome == EvaluationOutcome.PASS
    assert res_below.passed is True

    # Just above (35.01) -> FAIL
    res_above = evaluate_rule(rule, {**base_facts, "building_height_ft": 35.01})
    assert res_above.outcome == EvaluationOutcome.FAIL
    assert res_above.passed is False


def test_numeric_boundary_water_closet_gpf_lte(water_pack):
    # SJ-W-101: effective_flush_volume_gpf <= 1.28
    rule = next(r for r in water_pack["rules"] if r["rule_id"] == "SJ-W-101")
    base_facts = {"fixture_type": "water_closet"}

    assert evaluate_rule(rule, {**base_facts, "effective_flush_volume_gpf": 1.28}).outcome == EvaluationOutcome.PASS
    assert evaluate_rule(rule, {**base_facts, "effective_flush_volume_gpf": 1.27}).outcome == EvaluationOutcome.PASS
    assert evaluate_rule(rule, {**base_facts, "effective_flush_volume_gpf": 1.29}).outcome == EvaluationOutcome.FAIL


def test_numeric_boundary_gt_and_lt_operators():
    # Test condition evaluation directly for gt and lt
    cond_gt = {"field": "stories", "operator": "gt", "value": 2}
    assert evaluate_condition(cond_gt, {"stories": 2}) == (False, None)
    assert evaluate_condition(cond_gt, {"stories": 2.01}) == (True, None)
    assert evaluate_condition(cond_gt, {"stories": 3}) == (True, None)

    cond_lt = {"field": "area", "operator": "lt", "value": 10000}
    assert evaluate_condition(cond_lt, {"area": 9999}) == (True, None)
    assert evaluate_condition(cond_lt, {"area": 10000}) == (False, None)
    assert evaluate_condition(cond_lt, {"area": 10001}) == (False, None)


# ---------------------------------------------------------------------------
# 3. Conditional Branches (cases selection based on `when` conditions)
# ---------------------------------------------------------------------------

def test_zoning_setback_branches_by_district(zoning_pack):
    # SJ-Z-101: R-1-8 (20 ft), R-1-5 (20 ft), R-1-2 (30 ft), R-1-1 (30 ft), R-1-RR (50 ft)
    rule = next(r for r in zoning_pack["rules"] if r["rule_id"] == "SJ-Z-101")

    # 25 ft setback:
    # In R-1-8 (requires >= 20) -> PASS
    res_r18 = evaluate_rule(rule, {"zone_code": "R-1-8", "front_setback_ft": 25.0})
    assert res_r18.outcome == EvaluationOutcome.PASS

    # In R-1-2 (requires >= 30) -> FAIL
    res_r12 = evaluate_rule(rule, {"zone_code": "R-1-2", "front_setback_ft": 25.0})
    assert res_r12.outcome == EvaluationOutcome.FAIL

    # In R-1-RR (requires >= 50) -> FAIL
    res_rr = evaluate_rule(rule, {"zone_code": "R-1-RR", "front_setback_ft": 25.0})
    assert res_rr.outcome == EvaluationOutcome.FAIL

    # In R-1-RR with 55 ft setback -> PASS
    res_rr_pass = evaluate_rule(rule, {"zone_code": "R-1-RR", "front_setback_ft": 55.0})
    assert res_rr_pass.outcome == EvaluationOutcome.PASS


def test_fire_grade_floor_reduction_branch(fire_pack):
    # SJ-F-105:
    # when grade_floor_reduction_verified == False -> net_clear_area_sqft >= 5.7
    # when grade_floor_reduction_verified == True  -> net_clear_area_sqft >= 5.0
    rule = next(r for r in fire_pack["rules"] if r["rule_id"] == "SJ-F-105")
    base_facts = {"emergency_opening_required": True, "net_clear_area_sqft": 5.2}

    # Without grade floor reduction (requires 5.7) -> FAIL
    res_no_reduct = evaluate_rule(rule, {**base_facts, "grade_floor_reduction_verified": False})
    assert res_no_reduct.outcome == EvaluationOutcome.FAIL

    # With grade floor reduction (requires 5.0) -> PASS
    res_with_reduct = evaluate_rule(rule, {**base_facts, "grade_floor_reduction_verified": True})
    assert res_with_reduct.outcome == EvaluationOutcome.PASS


def test_structural_stories_bolt_spacing_branch(structural_pack):
    # SJ-S-102:
    # stories_count <= 2 -> max spacing <= 6 ft
    # stories_count > 2  -> max spacing <= 4 ft
    rule = next(r for r in structural_pack["rules"] if r["rule_id"] == "SJ-S-102")
    base_facts = {
        "prescriptive_crc_anchor_bolt_path": True,
        "engineered_alternative_anchorage": False,
        "maximum_bolt_spacing_ft": 5.0,
    }

    # 2 stories with 5 ft spacing -> PASS (<= 6 ft)
    res_2stories = evaluate_rule(rule, {**base_facts, "stories_count": 2})
    assert res_2stories.outcome == EvaluationOutcome.PASS

    # 3 stories with 5 ft spacing -> FAIL (> 4 ft)
    res_3stories = evaluate_rule(rule, {**base_facts, "stories_count": 3})
    assert res_3stories.outcome == EvaluationOutcome.FAIL

    # 3 stories with 4 ft spacing -> PASS (<= 4 ft)
    res_3stories_pass = evaluate_rule(rule, {**base_facts, "stories_count": 3, "maximum_bolt_spacing_ft": 4.0})
    assert res_3stories_pass.outcome == EvaluationOutcome.PASS


def test_no_matching_case_yields_requires_human_review(zoning_pack):
    # When facts have a known condition value that doesn't match any case
    rule = next(r for r in zoning_pack["rules"] if r["rule_id"] == "SJ-Z-101")
    # Rule applies_when is in ['R-1-8', 'R-1-5', 'R-1-2', 'R-1-1', 'R-1-RR']
    # If we create an artificial rule where applies_when passes but cases don't match:
    custom_rule = copy.deepcopy(rule)
    custom_rule["requirement"]["cases"] = [
        {"when": [{"field": "zone_code", "operator": "eq", "value": "SPECIAL"}], "checks": [{"field": "front_setback_ft", "operator": "gte", "value": 20}]}
    ]
    res = evaluate_rule(custom_rule, {"zone_code": "R-1-8", "front_setback_ft": 25.0})
    assert res.outcome == EvaluationOutcome.REQUIRES_HUMAN_REVIEW


def test_multiple_overlapping_cases_yield_requires_human_review(zoning_pack):
    rule = next(r for r in zoning_pack["rules"] if r["rule_id"] == "SJ-Z-101")
    custom_rule = copy.deepcopy(rule)
    # Create two overlapping cases that both match R-1-8
    case1 = {"when": [{"field": "zone_code", "operator": "eq", "value": "R-1-8"}], "checks": [{"field": "front_setback_ft", "operator": "gte", "value": 20}]}
    case2 = {"when": [{"field": "zone_code", "operator": "eq", "value": "R-1-8"}], "checks": [{"field": "front_setback_ft", "operator": "gte", "value": 25}]}
    custom_rule["requirement"]["cases"] = [case1, case2]
    res = evaluate_rule(custom_rule, {"zone_code": "R-1-8", "front_setback_ft": 25.0})
    assert res.outcome == EvaluationOutcome.REQUIRES_HUMAN_REVIEW
    assert "Multiple overlapping cases" in res.message


# ---------------------------------------------------------------------------
# 4. Calculations and Independent Checks
# ---------------------------------------------------------------------------

def test_emergency_escape_dimensions_vs_net_clear_area_independence(fire_pack):
    """A 20x24 in window satisfies minimum width (>=20) and height (>=24),
    but net clear area is 3.33 sqft, FAILING the independent area requirement (>=5.7).
    """
    rule_dim = next(r for r in fire_pack["rules"] if r["rule_id"] == "SJ-F-104")
    rule_area = next(r for r in fire_pack["rules"] if r["rule_id"] == "SJ-F-105")

    facts_pass_dim_fail_area = {
        "emergency_opening_required": True,
        "grade_floor_reduction_verified": False,
        "net_clear_width_in": 20.0,
        "net_clear_height_in": 24.0,
        "bottom_of_clear_opening_above_floor_in": 36.0,
    }

    # Evaluate dimension rule (SJ-F-104) -> PASS
    res_dim = evaluate_rule(rule_dim, facts_pass_dim_fail_area)
    assert res_dim.outcome == EvaluationOutcome.PASS
    assert res_dim.passed is True

    # Evaluate area rule (SJ-F-105) -> FAIL (area calculated is 3.333 sqft < 5.7)
    res_area = evaluate_rule(rule_area, facts_pass_dim_fail_area)
    assert res_area.outcome == EvaluationOutcome.FAIL
    assert res_area.passed is False
    assert "net_clear_area_sqft" in res_area.actual_value

    # Now provide a window with width=40, height=24 -> area = 6.67 sqft >= 5.7 -> both PASS
    facts_both_pass = {**facts_pass_dim_fail_area, "net_clear_width_in": 40.0}
    assert evaluate_rule(rule_dim, facts_both_pass).outcome == EvaluationOutcome.PASS
    assert evaluate_rule(rule_area, facts_both_pass).outcome == EvaluationOutcome.PASS

    # Now provide a window with width=18 (FAIL < 20), height=50 -> area = 6.25 sqft >= 5.7 (PASS)
    facts_fail_dim_pass_area = {**facts_pass_dim_fail_area, "net_clear_width_in": 18.0, "net_clear_height_in": 50.0}
    assert evaluate_rule(rule_dim, facts_fail_dim_pass_area).outcome == EvaluationOutcome.FAIL
    assert evaluate_rule(rule_area, facts_fail_dim_pass_area).outcome == EvaluationOutcome.PASS


def test_window_well_clear_area_calculation(fire_pack):
    rule = next(r for r in fire_pack["rules"] if r["rule_id"] == "SJ-F-106")
    base_facts = {
        "basement_emergency_window_well_required": True,
        "opening_can_fully_open": True,
    }

    # 36 in x 36 in -> 9.0 sqft >= 9.0 sqft -> PASS
    facts_pass = {**base_facts, "well_clear_width_in": 36.0, "well_clear_projection_in": 36.0}
    res_pass = evaluate_rule(rule, facts_pass)
    assert res_pass.outcome == EvaluationOutcome.PASS
    assert res_pass.calculation is not None

    # 36 in x 32 in -> 8.0 sqft < 9.0 sqft -> FAIL
    facts_fail = {**base_facts, "well_clear_width_in": 36.0, "well_clear_projection_in": 32.0}
    res_fail = evaluate_rule(rule, facts_fail)
    assert res_fail.outcome == EvaluationOutcome.FAIL


def test_sill_anchor_bolt_end_distance_calculation(structural_pack):
    rule = next(r for r in structural_pack["rules"] if r["rule_id"] == "SJ-S-104")
    base_facts = {
        "prescriptive_crc_anchor_bolt_path": True,
        "engineered_alternative_anchorage": False,
        "bolt_diameter_in": 0.5,
        "maximum_distance_from_each_plate_end_to_bolt_in": 10.0,
    }

    # 3.5 in end distance / 0.5 in dia = 7.0 bolt diameters >= 7 -> PASS
    res_pass = evaluate_rule(rule, {**base_facts, "bolt_end_distance_in": 3.5})
    assert res_pass.outcome == EvaluationOutcome.PASS

    # 3.0 in end distance / 0.5 in dia = 6.0 bolt diameters < 7 -> FAIL
    res_fail = evaluate_rule(rule, {**base_facts, "bolt_end_distance_in": 3.0})
    assert res_fail.outcome == EvaluationOutcome.FAIL


def test_impervious_replacement_percent_calculation(water_pack):
    rule = next(r for r in water_pack["rules"] if r["rule_id"] == "SJ-W-107")
    base_facts = {
        "regulated_redevelopment_project": True,
        "existing_impervious_area_sqft": 1000.0,
    }

    # 600 sqft replaced / 1000 sqft existing = 60% >= 50% -> triggers entire_site_in_treatment_scope
    facts_60 = {
        **base_facts,
        "replaced_impervious_area_sqft": 600.0,
        "entire_site_in_treatment_scope": True,
    }
    assert evaluate_rule(rule, facts_60).outcome == EvaluationOutcome.PASS

    # 400 sqft replaced / 1000 sqft existing = 40% < 50% -> triggers new_and_replaced_area_in_treatment_scope
    facts_40 = {
        **base_facts,
        "replaced_impervious_area_sqft": 400.0,
        "new_and_replaced_area_in_treatment_scope": True,
    }
    assert evaluate_rule(rule, facts_40).outcome == EvaluationOutcome.PASS


# ---------------------------------------------------------------------------
# 5. Missing Facts (treated as unknown, not zero/false -> NEEDS_INFORMATION)
# ---------------------------------------------------------------------------

def test_missing_check_fact_treated_as_unknown(structural_pack):
    rule = next(r for r in structural_pack["rules"] if r["rule_id"] == "SJ-S-101")
    facts_missing_embedment = {
        "prescriptive_crc_anchor_bolt_path": True,
        "engineered_alternative_anchorage": False,
        "bolt_diameter_in": 0.5,
        # "bolt_embedment_in" is missing
    }
    res = evaluate_rule(rule, facts_missing_embedment)
    assert res.outcome == EvaluationOutcome.NEEDS_INFORMATION
    assert res.passed is False
    assert res.severity == RuleSeverity.ADVISORY
    assert "Missing check field(s): bolt_embedment_in" in res.message


def test_missing_applicability_fact_yields_needs_information(water_pack):
    rule = next(r for r in water_pack["rules"] if r["rule_id"] == "SJ-W-101")
    # "fixture_type" is missing
    res = evaluate_rule(rule, {})
    assert res.outcome == EvaluationOutcome.NEEDS_INFORMATION
    assert res.passed is False
    assert "Cannot determine applicability" in res.message


def test_missing_case_when_fact_yields_needs_information(fire_pack):
    rule = next(r for r in fire_pack["rules"] if r["rule_id"] == "SJ-F-105")
    facts = {
        "emergency_opening_required": True,
        "net_clear_area_sqft": 5.5,
        # "grade_floor_reduction_verified" is missing
    }
    res = evaluate_rule(rule, facts)
    assert res.outcome == EvaluationOutcome.NEEDS_INFORMATION
    assert "Missing required fact(s) for conditional branch selection" in res.message


def test_get_mapped_and_missing_facts_documents_fields(base_permit, zoning_pack):
    mapped, missing = get_mapped_and_missing_facts(base_permit, zoning_pack)
    assert "building_height_ft" in mapped
    assert "lot_area_sqft" in mapped
    assert isinstance(missing, list)


# ---------------------------------------------------------------------------
# 6. Invalid Fact Types (booleans as numbers, NaN/Inf, zero preserved)
# ---------------------------------------------------------------------------

def test_booleans_rejected_as_numbers():
    with pytest.raises(ValueError, match="boolean value True is not allowed"):
        validate_numeric_measurement(True, "test_dim")

    with pytest.raises(ValueError, match="boolean value False is not allowed"):
        validate_numeric_measurement(False, "test_dim")


def test_evaluator_rejects_boolean_measurement_in_rule(structural_pack):
    rule = next(r for r in structural_pack["rules"] if r["rule_id"] == "SJ-S-101")
    facts_with_bool_num = {
        "prescriptive_crc_anchor_bolt_path": True,
        "engineered_alternative_anchorage": False,
        "bolt_diameter_in": True,  # Invalid: boolean for float
        "bolt_embedment_in": 7.0,
    }
    with pytest.raises(ValueError, match="boolean value True is not allowed"):
        evaluate_rule(rule, facts_with_bool_num)


def test_non_finite_floats_rejected():
    with pytest.raises(ValueError, match="non-finite float nan is not allowed"):
        validate_numeric_measurement(float("nan"), "test_nan")

    with pytest.raises(ValueError, match="non-finite float inf is not allowed"):
        validate_numeric_measurement(float("inf"), "test_inf")

    with pytest.raises(ValueError, match="non-finite float -inf is not allowed"):
        validate_numeric_measurement(float("-inf"), "test_neginf")


def test_non_numeric_types_rejected():
    with pytest.raises(ValueError, match="expected number, got str"):
        validate_numeric_measurement("ten", "string_val")

    with pytest.raises(ValueError, match="expected number, got list"):
        validate_numeric_measurement([10], "list_val")


def test_zero_preserved_as_valid_measurement():
    assert validate_numeric_measurement(0, "int_zero") == 0.0
    assert validate_numeric_measurement(0.0, "float_zero") == 0.0

    # 0.0 setback is evaluated as 0.0 ft, NOT None / missing
    cond = {"field": "front_setback_ft", "operator": "gte", "value": 20}
    matched, missing = evaluate_condition(cond, {"front_setback_ft": 0.0})
    assert missing is None
    assert matched is False  # 0.0 < 20, fails numeric check cleanly


# ---------------------------------------------------------------------------
# 7. Unknown Applicability, Unresolved Exceptions, Prerequisites
# ---------------------------------------------------------------------------

def test_evidence_proven_not_applicable_yields_not_applicable(structural_pack):
    rule = next(r for r in structural_pack["rules"] if r["rule_id"] == "SJ-S-101")
    facts = {
        "prescriptive_crc_anchor_bolt_path": False,  # Evidence proves rule does not apply
        "engineered_alternative_anchorage": False,
    }
    res = evaluate_rule(rule, facts)
    assert res.outcome == EvaluationOutcome.NOT_APPLICABLE
    assert res.passed is False
    assert res.severity == RuleSeverity.INFO


def test_unresolved_exception_yields_requires_human_review(zoning_pack):
    rule = next(r for r in zoning_pack["rules"] if r["rule_id"] == "SJ-Z-101")
    facts = {
        "zone_code": "R-1-8",
        "front_setback_ft": 25.0,
        "unresolved_exception": True,
    }
    res = evaluate_rule(rule, facts)
    assert res.outcome == EvaluationOutcome.REQUIRES_HUMAN_REVIEW
    assert "Claimed exception requires human review" in res.message


def test_unresolved_prerequisites_yield_requires_human_review(zoning_pack):
    rule = next(r for r in zoning_pack["rules"] if r["rule_id"] == "SJ-Z-101")
    facts = {
        "zone_code": "R-1-8",
        "front_setback_ft": 25.0,
        "prerequisites_satisfied": False,
    }
    res = evaluate_rule(rule, facts)
    assert res.outcome == EvaluationOutcome.REQUIRES_HUMAN_REVIEW
    assert "prerequisites not satisfied" in res.message


# ---------------------------------------------------------------------------
# 8. Empty Object Inventories
# ---------------------------------------------------------------------------

def test_empty_inventory_cannot_pass(water_pack):
    rule = next(r for r in water_pack["rules"] if r["rule_id"] == "SJ-W-101")
    res = evaluate_rule(rule, facts={}, inventory=[])
    assert res.outcome == EvaluationOutcome.NEEDS_INFORMATION
    assert res.passed is False
    assert "Empty object inventory cannot establish compliance" in res.message


def test_inventory_all_objects_passing(water_pack):
    rule = next(r for r in water_pack["rules"] if r["rule_id"] == "SJ-W-101")
    inv = [
        {"fixture_type": "water_closet", "effective_flush_volume_gpf": 1.28},
        {"fixture_type": "water_closet", "effective_flush_volume_gpf": 1.1},
    ]
    res = evaluate_rule(rule, facts={}, inventory=inv)
    assert res.outcome == EvaluationOutcome.PASS
    assert res.passed is True


def test_inventory_one_object_failing_causes_overall_fail(water_pack):
    rule = next(r for r in water_pack["rules"] if r["rule_id"] == "SJ-W-101")
    inv = [
        {"fixture_type": "water_closet", "effective_flush_volume_gpf": 1.28},
        {"fixture_type": "water_closet", "effective_flush_volume_gpf": 1.6},  # Violation
    ]
    res = evaluate_rule(rule, facts={}, inventory=inv)
    assert res.outcome == EvaluationOutcome.FAIL
    assert res.passed is False


# ---------------------------------------------------------------------------
# 9. Malformed YAML Rule Pack at Startup Raises ValueError
# ---------------------------------------------------------------------------

def test_malformed_pack_missing_department():
    with pytest.raises(ValueError, match="Missing or unsupported department"):
        load_and_validate_rule_pack({"rule_set_id": "test", "rules": []})


def test_malformed_pack_unsupported_department():
    with pytest.raises(ValueError, match="Missing or unsupported department"):
        load_and_validate_rule_pack({"department": "electrical", "rule_set_id": "test", "rules": []})


def test_malformed_pack_missing_rules():
    with pytest.raises(ValueError, match="must contain a non-empty 'rules' list"):
        load_and_validate_rule_pack(
            {
                "department": "zoning",
                "rule_set_id": "test",
                "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": False},
                "rules": [],
            }
        )


def test_malformed_pack_duplicate_rule_id():
    rule = {
        "rule_id": "DUP-01",
        "rule_name": "Duplicate Rule",
        "requirement": {"cases": [{"checks": [{"field": "x", "operator": "eq", "value": 1}]}]},
    }
    with pytest.raises(ValueError, match="Duplicate rule_id 'DUP-01'"):
        load_and_validate_rule_pack(
            {
                "department": "zoning",
                "rule_set_id": "test",
                "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": False},
                "rules": [rule, rule],
            }
        )


def test_malformed_pack_unsupported_operator():
    rule = {
        "rule_id": "BAD-OP",
        "rule_name": "Bad Operator Rule",
        "requirement": {"cases": [{"checks": [{"field": "x", "operator": "regex", "value": ".*"}]}]},
    }
    with pytest.raises(ValueError, match="unsupported operator 'regex'"):
        load_and_validate_rule_pack(
            {
                "department": "zoning",
                "rule_set_id": "test",
                "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": False},
                "rules": [rule],
            }
        )


def test_malformed_pack_operator_in_requires_collection():
    rule = {
        "rule_id": "BAD-IN",
        "rule_name": "Bad In Operator",
        "requirement": {"cases": [{"checks": [{"field": "x", "operator": "in", "value": "single_string"}]}]},
    }
    with pytest.raises(ValueError, match="operator 'in' requires a collection value"):
        load_and_validate_rule_pack(
            {
                "department": "zoning",
                "rule_set_id": "test",
                "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": False},
                "rules": [rule],
            }
        )


def test_malformed_pack_nonexistent_file():
    with pytest.raises(ValueError, match="Rule pack file not found"):
        load_and_validate_rule_pack("rules/ca-san-jose/non_existent.yaml")


# ---------------------------------------------------------------------------
# 10. Partial-Pack Approval Restrictions
# ---------------------------------------------------------------------------

def test_partial_pack_cannot_yield_approval_even_when_all_checks_pass(fire_pack):
    # Verify policy is False
    assert fire_pack["evaluation_policy"]["allow_final_department_approval_from_this_pack_alone"] is False

    # Create facts where all fire checks pass or are NOT_APPLICABLE
    all_passing_facts = {
        "qualifying_side_hinged_egress_doors": 1,
        "clear_width_in": 36.0,
        "clear_height_in": 80.0,
        "continuous_unobstructed_route": True,
        "passes_through_garage": False,
        "discharges_to_public_way_or_connected_yard_court": True,
        "openable_inside_without_key_special_knowledge_or_effort": True,
        "emergency_opening_required": False,
        "basement_emergency_window_well_required": False,
    }

    report = evaluate_department(fire_pack, facts=all_passing_facts)
    # Status must be INCOMPLETE, under no circumstances APPROVED
    assert report.status == "INCOMPLETE"
    assert report.is_incomplete is True
    assert report.is_approved is False
    assert report.is_rejected is False
    assert "partial rule pack policy" in report.summary_message


def test_full_pack_allows_approval_when_policy_enabled(fire_pack):
    pack_with_approval = copy.deepcopy(fire_pack)
    pack_with_approval["evaluation_policy"]["allow_final_department_approval_from_this_pack_alone"] = True

    all_passing_facts = {
        "qualifying_side_hinged_egress_doors": 1,
        "clear_width_in": 36.0,
        "clear_height_in": 80.0,
        "continuous_unobstructed_route": True,
        "passes_through_garage": False,
        "discharges_to_public_way_or_connected_yard_court": True,
        "openable_inside_without_key_special_knowledge_or_effort": True,
        "emergency_opening_required": False,
        "basement_emergency_window_well_required": False,
    }

    report = evaluate_department(pack_with_approval, facts=all_passing_facts)
    assert report.status == DepartmentStatus.APPROVED.value
    assert report.is_approved is True
    assert report.is_incomplete is False
    assert report.is_rejected is False


def test_partial_pack_violation_yields_rejection_not_incomplete(fire_pack):
    facts_with_violation = {
        "qualifying_side_hinged_egress_doors": 0,  # Violation: requires >= 1
    }
    report = evaluate_department(fire_pack, facts=facts_with_violation)
    assert report.status == DepartmentStatus.REJECTED.value
    assert report.is_rejected is True
    assert report.is_incomplete is False


# ---------------------------------------------------------------------------
# 11. Gemini Integration (Mocked, zero live billable calls)
# ---------------------------------------------------------------------------

def test_gemini_success_enriches_reasons_and_preserves_decision(base_permit_dict, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock-test-key")

    mock_gemini_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "explanations": {
                                    "SJ-F-101": "Gemini explanation: Information needed for qualifying side-hinged egress doors.",
                                    "SJ-F-102": "Gemini explanation: Information needed for clear dimensions of egress door.",
                                    "SJ-F-103": "Gemini explanation: Information needed for continuous unobstructed egress path.",
                                    "SJ-F-104": "Gemini explanation: Emergency opening dimensions require additional site evidence.",
                                    "SJ-F-105": "Gemini explanation: Net clear area evaluation requires window opening measurements.",
                                    "SJ-F-106": "Gemini explanation: Basement window well review requires dimension documentation.",
                                }
                            })
                        }
                    ],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-goog-api-key") == "mock-test-key"
        assert "key=" not in str(request.url)
        return httpx.Response(200, json=mock_gemini_payload)

    with TestClient(department_app("fire")) as client:
        # Patch client's app http_client
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(mock_transport_handler)
        )

        response = client.post(
            "/evaluate",
            json={
                "permit_id": "pm-gemini-success",
                "trace_id": "tr-gemini-success",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["detail"]["code"] == "INCOMPLETE_REVIEW"
        # Check that findings received the explanation
        findings_map = {f["rule_id"]: f["message"] for f in data["detail"]["findings"]}
        assert "SJ-F-101" in findings_map
        assert "Gemini explanation" in findings_map["SJ-F-101"]


def test_gemini_timeout_uses_fallback_and_preserves_decision(base_permit_dict, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock-test-key")

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Gemini deadline exceeded", request=request)

    with TestClient(department_app("fire")) as client:
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(timeout_handler)
        )

        response = client.post(
            "/evaluate",
            json={
                "permit_id": "pm-timeout",
                "trace_id": "tr-timeout",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["detail"]["code"] == "INCOMPLETE_REVIEW"
        # Findings should cleanly contain deterministic fallback messages
        assert len(data["detail"]["findings"]) == 6
        for f in data["detail"]["findings"]:
            assert f["message"] != ""


def test_gemini_provider_failure_uses_fallback_and_preserves_decision(base_permit_dict, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock-test-key")

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with TestClient(department_app("fire")) as client:
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(error_handler)
        )

        response = client.post(
            "/evaluate",
            json={
                "permit_id": "pm-500",
                "trace_id": "tr-500",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INCOMPLETE_REVIEW"


def test_gemini_malformed_json_uses_fallback(base_permit_dict, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock-test-key")

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "Not valid JSON at all"}]}}]
        })

    with TestClient(department_app("fire")) as client:
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(malformed_handler)
        )

        response = client.post(
            "/evaluate",
            json={
                "permit_id": "pm-bad-json",
                "trace_id": "tr-bad-json",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INCOMPLETE_REVIEW"


def test_gemini_missing_api_key_uses_fallback(base_permit_dict, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with TestClient(department_app("fire")) as client:
        response = client.post(
            "/evaluate",
            json={
                "permit_id": "pm-no-key",
                "trace_id": "tr-no-key",
                "department": "fire",
                "permit": base_permit_dict,
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INCOMPLETE_REVIEW"


# ---------------------------------------------------------------------------
# 12. Orchestrator Integration (Fanout with dev/orchestrator.py harness)
# ---------------------------------------------------------------------------

def test_orchestrator_fanout_with_incomplete_departments(tmp_path, base_permit_dict):
    """When departments return 422 INCOMPLETE_REVIEW, orchestrator marks each as ERROR,
    and final decision is REJECTED with INCOMPLETE_REVIEWS.
    """
    def mock_incomplete(request: httpx.Request) -> httpx.Response:
        dept = json.loads(request.content)["department"]
        return httpx.Response(
            422,
            json={
                "detail": {
                    "code": "INCOMPLETE_REVIEW",
                    "department": dept,
                    "message": "Evaluation incomplete",
                    "unresolved_reasons": [],
                    "findings": [],
                }
            },
        )

    db_path = tmp_path / "permits.db"
    with TestClient(orchestrator_app(db_path, transport=httpx.MockTransport(mock_incomplete))) as client:
        submit_res = client.post("/demo/submit", json=base_permit_dict)
        assert submit_res.status_code == 200
        result = submit_res.json()
        assert result["final_decision"] == "REJECTED"
        assert result["decision_code"] == "INCOMPLETE_REVIEWS"
        assert all(entry["status"] == "ERROR" for entry in result["department_results"].values())


def test_orchestrator_fanout_with_all_departments_approved(tmp_path, base_permit_dict):
    """When all 4 departments return 200 APPROVED, orchestrator yields APPROVED."""
    def mock_approved(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "permit_id": body["permit_id"],
                "department": body["department"],
                "status": "APPROVED",
                "execution_time_ms": 10.5,
                "reasons": [],
            },
        )

    db_path = tmp_path / "permits.db"
    with TestClient(orchestrator_app(db_path, transport=httpx.MockTransport(mock_approved))) as client:
        submit_res = client.post("/demo/submit", json=base_permit_dict)
        assert submit_res.status_code == 200
        result = submit_res.json()
        assert result["final_decision"] == "APPROVED"
        assert result["decision_code"] == "ALL_DEPARTMENTS_APPROVED"
        assert len(result["department_results"]) == 4
        assert all(entry["status"] == "APPROVED" for entry in result["department_results"].values())


def test_orchestrator_fanout_with_department_rejection(tmp_path, base_permit_dict):
    """When 3 departments approve and 1 rejects, orchestrator yields REJECTED with DEPARTMENT_REJECTIONS."""
    def mock_rejection(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        status = "REJECTED" if body["department"] == "zoning" else "APPROVED"
        return httpx.Response(
            200,
            json={
                "permit_id": body["permit_id"],
                "department": body["department"],
                "status": status,
                "execution_time_ms": 12.0,
                "reasons": [{"rule_id": "SJ-Z-101", "rule_name": "Setback", "passed": False, "severity": "VIOLATION", "message": "Setback too small"}] if status == "REJECTED" else [],
            },
        )

    db_path = tmp_path / "permits.db"
    with TestClient(orchestrator_app(db_path, transport=httpx.MockTransport(mock_rejection))) as client:
        submit_res = client.post("/demo/submit", json=base_permit_dict)
        assert submit_res.status_code == 200
        result = submit_res.json()
        assert result["final_decision"] == "REJECTED"
        assert result["decision_code"] == "DEPARTMENT_REJECTIONS"
        assert result["department_results"]["zoning"]["status"] == "REJECTED"
        assert result["department_results"]["fire"]["status"] == "APPROVED"


def test_orchestrator_fanout_with_department_timeout(tmp_path, base_permit_dict):
    """When a department request times out, orchestrator yields REJECTED with INCOMPLETE_REVIEWS."""
    def mock_timeout(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["department"] == "water":
            raise httpx.ReadTimeout("Water service timed out", request=request)
        return httpx.Response(
            200,
            json={
                "permit_id": body["permit_id"],
                "department": body["department"],
                "status": "APPROVED",
                "execution_time_ms": 15.0,
                "reasons": [],
            },
        )

    db_path = tmp_path / "permits.db"
    with TestClient(orchestrator_app(db_path, transport=httpx.MockTransport(mock_timeout))) as client:
        submit_res = client.post("/demo/submit", json=base_permit_dict)
        assert submit_res.status_code == 200
        result = submit_res.json()
        assert result["final_decision"] == "REJECTED"
        assert result["decision_code"] == "INCOMPLETE_REVIEWS"
        assert result["department_results"]["water"]["status"] == "TIMEOUT"


def test_orchestrator_fanout_dispatching_to_real_department_services(tmp_path, base_permit_dict):
    """End-to-end fanout connecting dev/orchestrator.py directly to real department apps."""
    # Pre-instantiate real department apps
    apps = {
        "zoning": department_app("zoning"),
        "fire": department_app("fire"),
        "water": department_app("water"),
        "structural": department_app("structural"),
    }
    clients = {name: TestClient(app) for name, app in apps.items()}

    for c in clients.values():
        c.__enter__()

    try:
        def dispatch_to_real_app(request: httpx.Request) -> httpx.Response:
            dept_name = json.loads(request.content)["department"]
            client = clients[dept_name]
            starlette_resp = client.post(
                "/evaluate",
                json=json.loads(request.content),
            )
            return httpx.Response(
                status_code=starlette_resp.status_code,
                content=starlette_resp.content,
                headers=starlette_resp.headers,
            )

        db_path = tmp_path / "permits.db"
        with TestClient(orchestrator_app(db_path, transport=httpx.MockTransport(dispatch_to_real_app))) as client:
            submit_res = client.post("/demo/submit", json=base_permit_dict)
            assert submit_res.status_code == 200
            result = submit_res.json()
            # Because all 4 department starter packs have allow_final_department_approval_from_this_pack_alone: false,
            # each returns 422 INCOMPLETE_REVIEW, so orchestrator treats them as ERROR and REJECTED
            assert result["final_decision"] == "REJECTED"
            assert result["decision_code"] == "INCOMPLETE_REVIEWS"
            assert set(result["department_results"].keys()) == {"zoning", "fire", "water", "structural"}
    finally:
        for c in clients.values():
            c.__exit__(None, None, None)


def test_applies_when_conjunction_false_and_unknown_is_not_applicable():
    """Verify propositional logic conjunction: False AND Unknown == False.

    When one condition is proven false with evidence, the rule is NOT_APPLICABLE,
    even if another applies_when condition has a missing/unknown fact.
    """
    rule = {
        "rule_id": "TEST-APP-001",
        "rule_name": "Commercial Occupancy Fire Alarm",
        "applies_when": [
            {"field": "project_type", "operator": "eq", "value": "commercial"},
            {"field": "occupancy_load", "operator": "gte", "value": 50},
        ],
        "check": {
            "field": "fire_alarm_system_provided",
            "operator": "eq",
            "value": True,
        },
    }
    # project_type is residential (condition 1 is FALSE with evidence)
    # occupancy_load is missing (condition 2 is UNKNOWN)
    # Result must be NOT_APPLICABLE, NOT NEEDS_INFORMATION
    res = evaluate_rule(rule, {"project_type": "residential"})
    assert res.outcome == EvaluationOutcome.NOT_APPLICABLE
    assert res.passed is False

