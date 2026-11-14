"""
Deterministic rule evaluation and decision aggregation for department rule packs.
Implements the 4 San José department starter packs (zoning, fire, water, structural).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import yaml

from contract.models import (
    DepartmentName,
    DepartmentStatus,
    EvaluationReason,
    Permit,
    RuleSeverity,
)


class EvaluationOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"


SUPPORTED_OPERATORS: Set[str] = {"eq", "in", "gte", "gt", "lte", "lt"}
SUPPORTED_DEPARTMENTS: Set[str] = {"zoning", "fire", "water", "structural"}


def validate_numeric_measurement(value: Any, field_name: str) -> float:
    """Validate that a value is a valid numeric measurement.

    Booleans must NOT count as numeric measurements (isinstance(v, bool) checked first).
    Reject non-finite floats (NaN, Inf).
    Preserve 0.0 as a valid measurement.
    """
    if isinstance(value, bool):
        raise ValueError(
            f"Invalid numeric measurement for '{field_name}': boolean value {value!r} is not allowed"
        )
    if not isinstance(value, (int, float)):
        raise ValueError(
            f"Invalid numeric measurement for '{field_name}': expected number, got {type(value).__name__} ({value!r})"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(
            f"Invalid numeric measurement for '{field_name}': non-finite float {value!r} is not allowed"
        )
    return float(value)


def load_and_validate_rule_pack(
    source: Union[str, Path, Dict[str, Any]],
    expected_department: Optional[Union[str, DepartmentName]] = None,
) -> Dict[str, Any]:
    """Parse and validate YAML rule format at startup.

    Supports rule_id, rule_name, applies_when, prerequisites, requirement
    (cases, when, checks, combine_checks="all", case_selection="exactly_one"),
    sources, exceptions_and_limits, evaluation_policy.
    Fails fast with ValueError on malformed or unsupported rules.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise ValueError(f"Rule pack file not found: {path}")
        with path.open("r", encoding="utf-8") as handle:
            pack = yaml.safe_load(handle)
    elif isinstance(source, dict):
        pack = source
    else:
        raise ValueError(f"Invalid rule pack source type: {type(source)}")

    if not isinstance(pack, dict):
        raise ValueError("Rule pack must be a YAML mapping/dictionary")

    # Validate top-level metadata
    dept_val = pack.get("department")
    if not dept_val or not isinstance(dept_val, str) or dept_val not in SUPPORTED_DEPARTMENTS:
        raise ValueError(f"Missing or unsupported department in rule pack: {dept_val}")

    if expected_department is not None:
        expected_str = (
            expected_department.value
            if isinstance(expected_department, DepartmentName)
            else str(expected_department)
        )
        if dept_val != expected_str:
            raise ValueError(
                f"Rule pack department '{dept_val}' does not match expected department '{expected_str}'"
            )

    if not pack.get("rule_set_id") or not isinstance(pack["rule_set_id"], str):
        raise ValueError("Rule pack missing required string field 'rule_set_id'")

    # Validate evaluation_policy
    policy = pack.get("evaluation_policy")
    if not isinstance(policy, dict):
        raise ValueError("Rule pack missing required dictionary 'evaluation_policy'")
    if "allow_final_department_approval_from_this_pack_alone" not in policy:
        raise ValueError(
            "evaluation_policy missing 'allow_final_department_approval_from_this_pack_alone'"
        )
    if not isinstance(policy["allow_final_department_approval_from_this_pack_alone"], bool):
        raise ValueError(
            "evaluation_policy 'allow_final_department_approval_from_this_pack_alone' must be a boolean"
        )

    # Validate rules list
    rules = pack.get("rules")
    if not isinstance(rules, list) or len(rules) == 0:
        raise ValueError("Rule pack must contain a non-empty 'rules' list")

    rule_ids: Set[str] = set()

    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule at index {idx} must be a dictionary")

        rule_id = rule.get("rule_id")
        if not rule_id or not isinstance(rule_id, str):
            raise ValueError(f"Rule at index {idx} missing valid 'rule_id'")
        if rule_id in rule_ids:
            raise ValueError(f"Duplicate rule_id '{rule_id}' detected in rule pack")
        rule_ids.add(rule_id)

        rule_name = rule.get("rule_name")
        if not rule_name or not isinstance(rule_name, str):
            raise ValueError(f"Rule '{rule_id}' missing valid 'rule_name'")

        # Validate applies_when
        applies_when = rule.get("applies_when", [])
        if not isinstance(applies_when, list):
            raise ValueError(f"Rule '{rule_id}' 'applies_when' must be a list")
        for c_idx, cond in enumerate(applies_when):
            if not isinstance(cond, dict):
                raise ValueError(f"Rule '{rule_id}' applies_when[{c_idx}] must be a dictionary")
            _validate_condition_schema(cond, f"Rule '{rule_id}' applies_when[{c_idx}]")

        # Validate prerequisites
        prereqs = rule.get("prerequisites", [])
        if not isinstance(prereqs, list):
            raise ValueError(f"Rule '{rule_id}' 'prerequisites' must be a list")

        # Validate requirement
        req = rule.get("requirement")
        if not isinstance(req, dict):
            raise ValueError(f"Rule '{rule_id}' missing dictionary 'requirement'")

        combine_checks = req.get("combine_checks", "all")
        if combine_checks != "all":
            raise ValueError(
                f"Rule '{rule_id}' unsupported combine_checks '{combine_checks}'; only 'all' is supported"
            )

        case_selection = req.get("case_selection", "exactly_one")
        if case_selection != "exactly_one":
            raise ValueError(
                f"Rule '{rule_id}' unsupported case_selection '{case_selection}'; only 'exactly_one' is supported"
            )

        cases = req.get("cases")
        if not isinstance(cases, list) or len(cases) == 0:
            raise ValueError(f"Rule '{rule_id}' requirement must contain non-empty 'cases' list")

        for case_idx, c in enumerate(cases):
            if not isinstance(c, dict):
                raise ValueError(f"Rule '{rule_id}' cases[{case_idx}] must be a dictionary")
            when_conds = c.get("when", [])
            if not isinstance(when_conds, list):
                raise ValueError(f"Rule '{rule_id}' cases[{case_idx}] 'when' must be a list")
            for w_idx, cond in enumerate(when_conds):
                if not isinstance(cond, dict):
                    raise ValueError(
                        f"Rule '{rule_id}' cases[{case_idx}] when[{w_idx}] must be a dictionary"
                    )
                _validate_condition_schema(
                    cond, f"Rule '{rule_id}' cases[{case_idx}] when[{w_idx}]"
                )

            checks = c.get("checks")
            if not isinstance(checks, list) or len(checks) == 0:
                raise ValueError(
                    f"Rule '{rule_id}' cases[{case_idx}] must have non-empty 'checks' list"
                )
            for ch_idx, check in enumerate(checks):
                if not isinstance(check, dict):
                    raise ValueError(
                        f"Rule '{rule_id}' cases[{case_idx}] checks[{ch_idx}] must be a dictionary"
                    )
                _validate_condition_schema(
                    check, f"Rule '{rule_id}' cases[{case_idx}] checks[{ch_idx}]"
                )

    return pack


def _validate_condition_schema(cond: Dict[str, Any], context: str) -> None:
    """Validate schema of a condition or check item."""
    field_name = cond.get("field")
    if not field_name or not isinstance(field_name, str):
        raise ValueError(f"{context} missing valid 'field' string")
    op = cond.get("operator")
    if not op or op not in SUPPORTED_OPERATORS:
        raise ValueError(
            f"{context} has unsupported operator '{op}'; supported: {sorted(SUPPORTED_OPERATORS)}"
        )
    if "value" not in cond:
        raise ValueError(f"{context} missing 'value'")
    if op == "in" and not isinstance(cond["value"], (list, set, tuple)):
        raise ValueError(f"{context} operator 'in' requires a collection value (list/tuple/set)")


@dataclass
class RuleEvaluationResult:
    """Preserved evaluation finding for a single rule."""

    rule_id: str
    rule_name: str
    outcome: EvaluationOutcome
    passed: bool
    actual_value: Any = None
    required_value: Any = None
    calculation: Optional[str] = None
    sources: Any = None
    evidence_refs: List[str] = field(default_factory=list)
    message: str = ""
    severity: RuleSeverity = RuleSeverity.VIOLATION

    def to_evaluation_reason(self) -> EvaluationReason:
        sev = self.severity
        if self.outcome == EvaluationOutcome.FAIL:
            sev = RuleSeverity.VIOLATION
        elif self.outcome in (
            EvaluationOutcome.NEEDS_INFORMATION,
            EvaluationOutcome.REQUIRES_HUMAN_REVIEW,
        ):
            sev = RuleSeverity.ADVISORY
        elif self.outcome in (EvaluationOutcome.PASS, EvaluationOutcome.NOT_APPLICABLE):
            sev = RuleSeverity.INFO
        return EvaluationReason(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            passed=self.passed,
            severity=sev,
            message=self.message,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "outcome": self.outcome.value,
            "passed": self.passed,
            "actual_value": self.actual_value,
            "required_value": self.required_value,
            "calculation": self.calculation,
            "sources": self.sources,
            "evidence_refs": self.evidence_refs,
            "message": self.message,
            "severity": (
                self.severity.value
                if isinstance(self.severity, RuleSeverity)
                else str(self.severity)
            ),
        }


@dataclass
class DepartmentEvaluationReport:
    """Aggregated department evaluation outcome and findings."""

    department: DepartmentName
    status: str  # "APPROVED", "REJECTED", "INCOMPLETE"
    is_approved: bool
    is_rejected: bool
    is_incomplete: bool
    findings: List[RuleEvaluationResult]
    reasons: List[EvaluationReason]
    unresolved_reasons: List[EvaluationReason]
    violations: List[RuleEvaluationResult]
    summary_message: str

    def apply_explanations(self, explanations: Dict[str, str]) -> None:
        """Update findings and reasons with Gemini explanations when provided."""
        if not explanations:
            return
        reason_map = {r.rule_id: r for r in self.reasons}
        unresolved_map = {r.rule_id: r for r in self.unresolved_reasons}
        for finding in self.findings:
            if finding.rule_id in explanations:
                text = explanations[finding.rule_id]
                finding.message = text
                if finding.rule_id in reason_map:
                    reason_map[finding.rule_id].message = text
                if finding.rule_id in unresolved_map:
                    unresolved_map[finding.rule_id].message = text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "department": (
                self.department.value
                if isinstance(self.department, DepartmentName)
                else str(self.department)
            ),
            "status": self.status,
            "is_approved": self.is_approved,
            "is_rejected": self.is_rejected,
            "is_incomplete": self.is_incomplete,
            "findings": [f.to_dict() for f in self.findings],
            "reasons": [r.model_dump(mode="json") for r in self.reasons],
            "unresolved_reasons": [r.model_dump(mode="json") for r in self.unresolved_reasons],
            "violations": [f.to_dict() for f in self.violations],
            "summary_message": self.summary_message,
        }


def map_permit_to_facts(permit: Permit) -> Dict[str, Any]:
    """Map facts only from fields actually supplied by contract/models.py Permit.

    Treat missing facts as unknown (None), never default to 0 or False.
    """
    facts: Dict[str, Any] = {}

    # Applicant
    if permit.applicant:
        facts["applicant_name"] = permit.applicant.name
        facts["applicant_role"] = (
            permit.applicant.role.value
            if hasattr(permit.applicant.role, "value")
            else str(permit.applicant.role)
        )
        facts["applicant_email"] = permit.applicant.email
        facts["applicant_phone"] = permit.applicant.phone

    # Property address
    if permit.property_address:
        facts["property_address_street"] = permit.property_address.street
        facts["property_address_city"] = permit.property_address.city
        facts["property_address_state"] = permit.property_address.state
        facts["property_address_zip"] = permit.property_address.zip

    # General permit
    facts["permit_id"] = permit.permit_id
    facts["project_title"] = permit.project_title
    facts["project_description"] = permit.project_description
    facts["estimated_cost_usd"] = permit.estimated_cost_usd

    # Zoning
    if permit.zoning:
        facts["zone_code"] = (
            permit.zoning.zone_code.value
            if hasattr(permit.zoning.zone_code, "value")
            else str(permit.zoning.zone_code)
        )
        facts["building_height_ft"] = permit.zoning.building_height_ft
        facts["lot_area_sqft"] = permit.zoning.lot_area_sqft
        facts["building_footprint_sqft"] = permit.zoning.building_footprint_sqft
        facts["front_setback_ft"] = permit.zoning.setback_front_ft
        facts["setback_front_ft"] = permit.zoning.setback_front_ft
        facts["rear_setback_ft"] = permit.zoning.setback_rear_ft
        facts["setback_rear_ft"] = permit.zoning.setback_rear_ft
        facts["interior_side_setback_ft"] = permit.zoning.setback_side_ft
        facts["setback_side_ft"] = permit.zoning.setback_side_ft

    # Fire
    if permit.fire:
        facts["occupancy_type"] = (
            permit.fire.occupancy_type.value
            if hasattr(permit.fire.occupancy_type, "value")
            else str(permit.fire.occupancy_type)
        )
        facts["occupant_load"] = permit.fire.occupant_load
        facts["has_sprinkler_system"] = permit.fire.has_sprinkler_system
        facts["egress_exits_count"] = permit.fire.egress_exits_count
        facts["distance_to_hydrant_ft"] = permit.fire.distance_to_hydrant_ft
        facts["has_fire_alarm"] = permit.fire.has_fire_alarm

    # Water
    if permit.water:
        facts["water_meter_size_inches"] = permit.water.water_meter_size_inches
        facts["daily_water_usage_gpd"] = permit.water.daily_water_usage_gpd
        facts["has_backflow_preventer"] = permit.water.has_backflow_preventer
        facts["has_stormwater_plan"] = permit.water.has_stormwater_plan
        facts["stormwater_control_plan_submitted"] = permit.water.has_stormwater_plan

    # Structural
    if permit.structural:
        facts["stories_count"] = permit.structural.stories_count
        facts["construction_type"] = (
            permit.structural.construction_type.value
            if hasattr(permit.structural.construction_type, "value")
            else str(permit.structural.construction_type)
        )
        facts["seismic_design_category"] = (
            permit.structural.seismic_design_category.value
            if hasattr(permit.structural.seismic_design_category, "value")
            else str(permit.structural.seismic_design_category)
        )
        facts["foundation_type"] = (
            permit.structural.foundation_type.value
            if hasattr(permit.structural.foundation_type, "value")
            else str(permit.structural.foundation_type)
        )

    return facts


def get_mapped_and_missing_facts(
    permit: Permit, rule_pack: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], List[str]]:
    """Document mapped facts vs missing facts required by the rule pack."""
    mapped = map_permit_to_facts(permit)
    if not rule_pack:
        return mapped, []

    required_fields: Set[str] = set()
    for rule in rule_pack.get("rules", []):
        for cond in rule.get("applies_when", []):
            required_fields.add(cond.get("field"))
        for c in rule.get("requirement", {}).get("cases", []):
            for w in c.get("when", []):
                required_fields.add(w.get("field"))
            for ch in c.get("checks", []):
                required_fields.add(ch.get("field"))

    required_fields.discard(None)
    missing = sorted([f for f in required_fields if f not in mapped or mapped[f] is None])
    return mapped, missing


def perform_explicit_calculations(
    facts: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Perform explicit calculations without eval or arbitrary code execution.

    Examples:
    - fire emergency opening clear area sqft = clear_width_in * clear_height_in / 144.0
    - basement window well clear area sqft = well_clear_width_in * well_clear_projection_in / 144.0
    - sill anchor end distance in bolt diameters = end_distance_in / bolt_diameter_in
    - impervious replacement percent = (replaced_sqft / existing_sqft) * 100.0
    """
    updated_facts = dict(facts)
    calcs: Dict[str, str] = {}

    # 1. Fire emergency opening clear area
    if (
        "net_clear_area_sqft" not in updated_facts
        or updated_facts["net_clear_area_sqft"] is None
    ):
        w_field = (
            "net_clear_width_in"
            if "net_clear_width_in" in updated_facts
            else ("clear_width_in" if "clear_width_in" in updated_facts else None)
        )
        h_field = (
            "net_clear_height_in"
            if "net_clear_height_in" in updated_facts
            else ("clear_height_in" if "clear_height_in" in updated_facts else None)
        )
        if (
            w_field
            and h_field
            and updated_facts[w_field] is not None
            and updated_facts[h_field] is not None
        ):
            w = validate_numeric_measurement(updated_facts[w_field], w_field)
            h = validate_numeric_measurement(updated_facts[h_field], h_field)
            area = (w * h) / 144.0
            updated_facts["net_clear_area_sqft"] = area
            calcs["net_clear_area_sqft"] = (
                f"{w_field} ({w} in) * {h_field} ({h} in) / 144.0 = {area:.4g} sqft"
            )

    # 2. Basement window well clear area
    if (
        "well_clear_area_sqft" not in updated_facts
        or updated_facts["well_clear_area_sqft"] is None
    ):
        if (
            "well_clear_width_in" in updated_facts
            and "well_clear_projection_in" in updated_facts
            and updated_facts["well_clear_width_in"] is not None
            and updated_facts["well_clear_projection_in"] is not None
        ):
            w = validate_numeric_measurement(
                updated_facts["well_clear_width_in"], "well_clear_width_in"
            )
            p = validate_numeric_measurement(
                updated_facts["well_clear_projection_in"], "well_clear_projection_in"
            )
            area = (w * p) / 144.0
            updated_facts["well_clear_area_sqft"] = area
            calcs["well_clear_area_sqft"] = (
                f"well_clear_width_in ({w} in) * well_clear_projection_in ({p} in) / 144.0 = {area:.4g} sqft"
            )

    # 3. Sill anchor end distance in bolt diameters
    if (
        "minimum_end_distance_in_bolt_diameters" not in updated_facts
        or updated_facts["minimum_end_distance_in_bolt_diameters"] is None
    ):
        ed_field = None
        for cand in (
            "bolt_end_distance_in",
            "minimum_end_distance_in",
            "end_distance_in",
            "plate_end_to_bolt_in",
        ):
            if cand in updated_facts and updated_facts[cand] is not None:
                ed_field = cand
                break
        if (
            ed_field
            and "bolt_diameter_in" in updated_facts
            and updated_facts["bolt_diameter_in"] is not None
        ):
            ed = validate_numeric_measurement(updated_facts[ed_field], ed_field)
            dia = validate_numeric_measurement(
                updated_facts["bolt_diameter_in"], "bolt_diameter_in"
            )
            if dia > 0:
                diameters = ed / dia
                updated_facts["minimum_end_distance_in_bolt_diameters"] = diameters
                calcs["minimum_end_distance_in_bolt_diameters"] = (
                    f"{ed_field} ({ed} in) / bolt_diameter_in ({dia} in) = {diameters:.4g} bolt_diameters"
                )

    # 4. Impervious replacement percent
    if (
        "impervious_replacement_percent" not in updated_facts
        or updated_facts["impervious_replacement_percent"] is None
    ):
        if (
            "replaced_impervious_area_sqft" in updated_facts
            and "existing_impervious_area_sqft" in updated_facts
            and updated_facts["replaced_impervious_area_sqft"] is not None
            and updated_facts["existing_impervious_area_sqft"] is not None
        ):
            rep = validate_numeric_measurement(
                updated_facts["replaced_impervious_area_sqft"], "replaced_impervious_area_sqft"
            )
            exist = validate_numeric_measurement(
                updated_facts["existing_impervious_area_sqft"], "existing_impervious_area_sqft"
            )
            if exist > 0:
                pct = (rep / exist) * 100.0
                updated_facts["impervious_replacement_percent"] = pct
                calcs["impervious_replacement_percent"] = (
                    f"({rep} sqft / {exist} sqft) * 100.0 = {pct:.4g}%"
                )

    return updated_facts, calcs


def evaluate_condition(
    cond: Dict[str, Any], facts: Dict[str, Any]
) -> Tuple[Optional[bool], Optional[str]]:
    """Evaluate a single condition against facts.

    Returns:
    (True, None) if condition matches.
    (False, None) if condition fails with evidence.
    (None, missing_field_name) if fact is unknown / missing.
    """
    field_name = cond["field"]
    op = cond["operator"]
    target = cond["value"]

    if field_name not in facts or facts[field_name] is None:
        return None, field_name

    actual = facts[field_name]

    if op in ("gte", "gt", "lte", "lt"):
        actual_num = validate_numeric_measurement(actual, field_name)
        target_num = validate_numeric_measurement(target, f"{field_name}_threshold")
        if op == "gte":
            return (actual_num >= target_num, None)
        if op == "gt":
            return (actual_num > target_num, None)
        if op == "lte":
            return (actual_num <= target_num, None)
        if op == "lt":
            return (actual_num < target_num, None)

    if op == "eq":
        if isinstance(target, bool):
            if not isinstance(actual, bool):
                return (False, None)
            return (actual == target, None)
        if isinstance(target, (int, float)):
            actual_num = validate_numeric_measurement(actual, field_name)
            target_num = validate_numeric_measurement(target, f"{field_name}_threshold")
            return (actual_num == target_num, None)
        return (str(actual) == str(target), None)

    if op == "in":
        # Membership check: ensure booleans are not treated as 1/0
        if isinstance(actual, bool):
            matched = any(isinstance(x, bool) and x == actual for x in target)
            return (matched, None)
        if isinstance(actual, (int, float)):
            actual_num = validate_numeric_measurement(actual, field_name)
            matched = any(
                isinstance(x, (int, float))
                and not isinstance(x, bool)
                and validate_numeric_measurement(x, "target_item") == actual_num
                for x in target
            )
            return (matched, None)
        matched = str(actual) in [str(x) for x in target]
        return (matched, None)

    raise ValueError(f"Unsupported operator: {op}")


def evaluate_rule(
    rule: Dict[str, Any],
    facts: Dict[str, Any],
    inventory: Optional[Sequence[Dict[str, Any]]] = None,
    evidence_refs: Optional[List[str]] = None,
) -> RuleEvaluationResult:
    """Evaluate a single rule against facts or an object inventory.

    Handles:
    - Object inventories: empty collection cannot produce a pass.
    - Applicability: requires evidence; unknown applicability -> NEEDS_INFORMATION.
    - Conditional cases: match exactly one; missing facts -> NEEDS_INFORMATION;
      overlapping or 0 matches -> REQUIRES_HUMAN_REVIEW.
    - Check evaluation: combine_checks="all", operators eq, in, gte, gt, lte, lt.
    - Fallback messages and result preservation.
    """
    rule_id = rule["rule_id"]
    rule_name = rule["rule_name"]
    sources = rule.get("sources") or rule.get("source")
    refs = list(evidence_refs) if evidence_refs else []

    # 8. Object inventories: evaluate every relevant affected object
    if inventory is not None:
        if len(inventory) == 0:
            return RuleEvaluationResult(
                rule_id=rule_id,
                rule_name=rule_name,
                outcome=EvaluationOutcome.NEEDS_INFORMATION,
                passed=False,
                sources=sources,
                evidence_refs=refs,
                message=f"{rule_id} ({rule_name}): NEEDS_INFORMATION. Empty object inventory cannot establish compliance.",
                severity=RuleSeverity.ADVISORY,
            )

        # Evaluate each object in inventory
        results = [
            evaluate_rule(rule, {**facts, **obj}, inventory=None, evidence_refs=refs)
            for obj in inventory
        ]

        # Inventory outcome aggregation:
        # If any FAIL -> FAIL
        for r in results:
            if r.outcome == EvaluationOutcome.FAIL:
                return RuleEvaluationResult(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    outcome=EvaluationOutcome.FAIL,
                    passed=False,
                    actual_value=r.actual_value,
                    required_value=r.required_value,
                    calculation=r.calculation,
                    sources=sources,
                    evidence_refs=refs,
                    message=f"{rule_id} ({rule_name}): FAIL. Object inventory violation: {r.message}",
                    severity=RuleSeverity.VIOLATION,
                )
        # If any NEEDS_INFORMATION -> NEEDS_INFORMATION
        for r in results:
            if r.outcome == EvaluationOutcome.NEEDS_INFORMATION:
                return RuleEvaluationResult(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    outcome=EvaluationOutcome.NEEDS_INFORMATION,
                    passed=False,
                    sources=sources,
                    evidence_refs=refs,
                    message=f"{rule_id} ({rule_name}): NEEDS_INFORMATION. Unresolved object in inventory: {r.message}",
                    severity=RuleSeverity.ADVISORY,
                )
        # If any REQUIRES_HUMAN_REVIEW -> REQUIRES_HUMAN_REVIEW
        for r in results:
            if r.outcome == EvaluationOutcome.REQUIRES_HUMAN_REVIEW:
                return RuleEvaluationResult(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    outcome=EvaluationOutcome.REQUIRES_HUMAN_REVIEW,
                    passed=False,
                    sources=sources,
                    evidence_refs=refs,
                    message=f"{rule_id} ({rule_name}): REQUIRES_HUMAN_REVIEW. Inventory object requires human review: {r.message}",
                    severity=RuleSeverity.ADVISORY,
                )
        # If all NOT_APPLICABLE -> NOT_APPLICABLE
        if all(r.outcome == EvaluationOutcome.NOT_APPLICABLE for r in results):
            return RuleEvaluationResult(
                rule_id=rule_id,
                rule_name=rule_name,
                outcome=EvaluationOutcome.NOT_APPLICABLE,
                passed=False,
                sources=sources,
                evidence_refs=refs,
                message=f"{rule_id} ({rule_name}): NOT_APPLICABLE. Rule conditions not applicable to any object in inventory.",
                severity=RuleSeverity.INFO,
            )
        # Otherwise all passed
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.PASS,
            passed=True,
            sources=sources,
            evidence_refs=refs,
            message=f"{rule_id} ({rule_name}): PASS. All {len(inventory)} objects in inventory satisfy requirements.",
            severity=RuleSeverity.INFO,
        )

    # Single-instance evaluation
    # Perform calculations first
    facts_with_calcs, calcs_map = perform_explicit_calculations(facts)

    # 4. Applicability: applies_when conditions combined with AND.
    applies_when = rule.get("applies_when", [])
    if applies_when:
        missing_applies_fields: List[str] = []
        condition_failed_with_evidence = False

        for cond in applies_when:
            matched, missing_field = evaluate_condition(cond, facts_with_calcs)
            if missing_field:
                missing_applies_fields.append(missing_field)
            elif matched is False:
                condition_failed_with_evidence = True

        # Require evidence before declaring NOT_APPLICABLE
        # If condition facts are missing/unknown, applicability is unknown -> outcome NEEDS_INFORMATION
        if missing_applies_fields:
            return RuleEvaluationResult(
                rule_id=rule_id,
                rule_name=rule_name,
                outcome=EvaluationOutcome.NEEDS_INFORMATION,
                passed=False,
                sources=sources,
                evidence_refs=refs,
                message=(
                    f"{rule_id} ({rule_name}): NEEDS_INFORMATION. Cannot determine applicability; "
                    f"missing condition fact(s): {', '.join(missing_applies_fields)}."
                ),
                severity=RuleSeverity.ADVISORY,
            )

        if condition_failed_with_evidence:
            return RuleEvaluationResult(
                rule_id=rule_id,
                rule_name=rule_name,
                outcome=EvaluationOutcome.NOT_APPLICABLE,
                passed=False,
                sources=sources,
                evidence_refs=refs,
                message=(
                    f"{rule_id} ({rule_name}): NOT_APPLICABLE. Scope/applies_when conditions not met "
                    "based on project evidence."
                ),
                severity=RuleSeverity.INFO,
            )

    # Check for explicit unresolved exceptions / prerequisites flags
    if facts_with_calcs.get("unresolved_exception") is True:
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.REQUIRES_HUMAN_REVIEW,
            passed=False,
            sources=sources,
            evidence_refs=refs,
            message=f"{rule_id} ({rule_name}): REQUIRES_HUMAN_REVIEW. Claimed exception requires human review.",
            severity=RuleSeverity.ADVISORY,
        )
    if facts_with_calcs.get("prerequisites_satisfied") is False:
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.REQUIRES_HUMAN_REVIEW,
            passed=False,
            sources=sources,
            evidence_refs=refs,
            message=f"{rule_id} ({rule_name}): REQUIRES_HUMAN_REVIEW. Rule prerequisites not satisfied by project evidence.",
            severity=RuleSeverity.ADVISORY,
        )

    # 5. Conditional Cases: match exactly one case by when conditions.
    req = rule["requirement"]
    cases = req["cases"]

    matching_cases: List[Dict[str, Any]] = []
    missing_when_fields: List[str] = []

    for c in cases:
        when_conds = c.get("when", [])
        if not when_conds:
            # Empty when means default unconditional branch
            matching_cases.append(c)
            continue

        case_matches = True
        for cond in when_conds:
            matched, missing_field = evaluate_condition(cond, facts_with_calcs)
            if missing_field:
                missing_when_fields.append(missing_field)
                case_matches = False
                break
            if not matched:
                case_matches = False
                break

        if case_matches:
            matching_cases.append(c)

    # Missing facts in condition -> NEEDS_INFORMATION
    if missing_when_fields:
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.NEEDS_INFORMATION,
            passed=False,
            sources=sources,
            evidence_refs=refs,
            message=(
                f"{rule_id} ({rule_name}): NEEDS_INFORMATION. Missing required fact(s) "
                f"for conditional branch selection: {', '.join(sorted(set(missing_when_fields)))}."
            ),
            severity=RuleSeverity.ADVISORY,
        )

    # Overlapping cases or no matching case -> REQUIRES_HUMAN_REVIEW
    if len(matching_cases) == 0:
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.REQUIRES_HUMAN_REVIEW,
            passed=False,
            sources=sources,
            evidence_refs=refs,
            message=f"{rule_id} ({rule_name}): REQUIRES_HUMAN_REVIEW. No matching case branch for project conditions.",
            severity=RuleSeverity.ADVISORY,
        )
    if len(matching_cases) > 1:
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.REQUIRES_HUMAN_REVIEW,
            passed=False,
            sources=sources,
            evidence_refs=refs,
            message=f"{rule_id} ({rule_name}): REQUIRES_HUMAN_REVIEW. Multiple overlapping cases matched ({len(matching_cases)} cases).",
            severity=RuleSeverity.ADVISORY,
        )

    selected_case = matching_cases[0]
    checks = selected_case.get("checks", [])

    # 6. Check evaluation: operators eq, in, gte, gt, lte, lt. combine_checks: "all".
    failed_checks: List[Dict[str, Any]] = []
    missing_check_fields: List[str] = []
    actual_values: Dict[str, Any] = {}
    required_values: Dict[str, Any] = {}
    active_calcs: List[str] = []

    for check in checks:
        f_name = check["field"]
        op = check["operator"]
        target = check["value"]
        unit = check.get("unit", "")

        required_values[f_name] = f"{op} {target} {unit}".strip()

        if f_name not in facts_with_calcs or facts_with_calcs[f_name] is None:
            missing_check_fields.append(f_name)
            continue

        actual_val = facts_with_calcs[f_name]
        actual_values[f_name] = f"{actual_val} {unit}".strip()

        if f_name in calcs_map:
            active_calcs.append(calcs_map[f_name])

        matched, _ = evaluate_condition(check, facts_with_calcs)
        if not matched:
            failed_checks.append(
                {
                    "field": f_name,
                    "operator": op,
                    "target": target,
                    "actual": actual_val,
                    "unit": unit,
                }
            )

    calc_summary = "; ".join(active_calcs) if active_calcs else None

    # Priority:
    # 1. Any check fails -> FAIL
    if failed_checks:
        fail_details = [
            f"{c['field']} ({c['actual']} {c['unit']}) does not satisfy {c['operator']} {c['target']} {c['unit']}".strip()
            for c in failed_checks
        ]
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.FAIL,
            passed=False,
            actual_value=actual_values,
            required_value=required_values,
            calculation=calc_summary,
            sources=sources,
            evidence_refs=refs,
            message=f"{rule_id} ({rule_name}): FAIL. Violation: {'; '.join(fail_details)}.",
            severity=RuleSeverity.VIOLATION,
        )

    # 2. Any check missing -> NEEDS_INFORMATION
    if missing_check_fields:
        return RuleEvaluationResult(
            rule_id=rule_id,
            rule_name=rule_name,
            outcome=EvaluationOutcome.NEEDS_INFORMATION,
            passed=False,
            actual_value=actual_values,
            required_value=required_values,
            calculation=calc_summary,
            sources=sources,
            evidence_refs=refs,
            message=(
                f"{rule_id} ({rule_name}): NEEDS_INFORMATION. Missing check field(s): "
                f"{', '.join(sorted(set(missing_check_fields)))}."
            ),
            severity=RuleSeverity.ADVISORY,
        )

    # 3. All checks pass -> PASS
    return RuleEvaluationResult(
        rule_id=rule_id,
        rule_name=rule_name,
        outcome=EvaluationOutcome.PASS,
        passed=True,
        actual_value=actual_values,
        required_value=required_values,
        calculation=calc_summary,
        sources=sources,
        evidence_refs=refs,
        message=f"{rule_id} ({rule_name}): PASS. Requirements satisfied ({', '.join(required_values.values())}).",
        severity=RuleSeverity.INFO,
    )


def evaluate_department(
    rule_pack: Dict[str, Any],
    permit: Optional[Permit] = None,
    facts: Optional[Dict[str, Any]] = None,
    inventories: Optional[Dict[str, Sequence[Dict[str, Any]]]] = None,
    evidence_refs: Optional[List[str]] = None,
) -> DepartmentEvaluationReport:
    """Evaluate a department rule pack and aggregate department decision.

    Requirements:
    - Any check is FAIL -> REJECTED (RuleSeverity.VIOLATION).
    - All checks pass, but allow_final_department_approval_from_this_pack_alone is False
      (or unresolved NEEDS_INFORMATION / REQUIRES_HUMAN_REVIEW) -> review is UNRESOLVED / INCOMPLETE.
      Under no circumstances grant APPROVED from this partial pack alone.
    """
    dept_name = DepartmentName(rule_pack["department"])
    policy = rule_pack.get("evaluation_policy", {})
    allow_final_approval = policy.get(
        "allow_final_department_approval_from_this_pack_alone", False
    )

    # Aggregate base facts
    combined_facts: Dict[str, Any] = {}
    if permit is not None:
        combined_facts.update(map_permit_to_facts(permit))
    if facts:
        combined_facts.update(facts)

    inv_map = inventories or {}
    findings: List[RuleEvaluationResult] = []

    for rule in rule_pack.get("rules", []):
        rid = rule["rule_id"]
        inv = inv_map.get(rid)
        finding = evaluate_rule(
            rule, combined_facts, inventory=inv, evidence_refs=evidence_refs
        )
        findings.append(finding)

    violations = [f for f in findings if f.outcome == EvaluationOutcome.FAIL]
    unresolved = [
        f
        for f in findings
        if f.outcome
        in (EvaluationOutcome.NEEDS_INFORMATION, EvaluationOutcome.REQUIRES_HUMAN_REVIEW)
    ]
    passed_or_na = [
        f
        for f in findings
        if f.outcome in (EvaluationOutcome.PASS, EvaluationOutcome.NOT_APPLICABLE)
    ]

    all_reasons = [f.to_evaluation_reason() for f in findings]
    violation_reasons = [f.to_evaluation_reason() for f in violations]
    unresolved_reasons = [f.to_evaluation_reason() for f in unresolved]

    # Aggregation logic:
    # 1. Any check is FAIL -> REJECTED
    if violations:
        status = DepartmentStatus.REJECTED.value
        is_rejected = True
        is_incomplete = False
        is_approved = False
        summary = (
            f"Department {dept_name.value} REJECTED with {len(violations)} verified violation(s)."
        )
        reasons_to_report = violation_reasons

    # 2. Incomplete if allow_final_approval is False OR unresolved findings exist
    elif not allow_final_approval or unresolved:
        status = "INCOMPLETE"
        is_rejected = False
        is_incomplete = True
        is_approved = False
        if unresolved:
            summary = (
                f"Department {dept_name.value} review INCOMPLETE: {len(unresolved)} rule(s) "
                "require additional information or human review."
            )
        else:
            summary = (
                f"Department {dept_name.value} review INCOMPLETE: partial rule pack policy "
                "(allow_final_department_approval_from_this_pack_alone is false) disables final approval."
            )
        reasons_to_report = unresolved_reasons if unresolved_reasons else all_reasons

    # 3. Only grant APPROVED when allow_final_approval is True and all checks pass/NA
    else:
        status = DepartmentStatus.APPROVED.value
        is_approved = True
        is_rejected = False
        is_incomplete = False
        summary = f"Department {dept_name.value} APPROVED. All checks satisfied."
        reasons_to_report = all_reasons

    return DepartmentEvaluationReport(
        department=dept_name,
        status=status,
        is_approved=is_approved,
        is_rejected=is_rejected,
        is_incomplete=is_incomplete,
        findings=findings,
        reasons=reasons_to_report,
        unresolved_reasons=unresolved_reasons,
        violations=violations,
        summary_message=summary,
    )
