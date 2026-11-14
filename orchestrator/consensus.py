"""Consensus engine for Permit Orchestrator."""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

from contract.models import (
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    FinalDecision,
)

REQUIRED_DEPARTMENTS: Tuple[str, ...] = (
    DepartmentName.ZONING.value,
    DepartmentName.FIRE.value,
    DepartmentName.WATER.value,
    DepartmentName.STRUCTURAL.value,
)


def _normalize_department_name(name: Any) -> str:
    """Normalize department name to lowercase string."""
    if isinstance(name, DepartmentName):
        return name.value
    if hasattr(name, "value") and isinstance(name.value, str):
        return name.value.lower()
    return str(name).lower()


def _extract_status(res: Any) -> Optional[DepartmentStatus]:
    """Safely extract DepartmentStatus enum from summary object or dict."""
    if res is None:
        return None
    status = getattr(res, "status", None)
    if status is None and isinstance(res, dict):
        status = res.get("status")
    if isinstance(status, str):
        try:
            return DepartmentStatus(status)
        except ValueError:
            try:
                return DepartmentStatus(status.strip().upper())
            except ValueError:
                return None
    if isinstance(status, DepartmentStatus):
        return status
    return None


def _extract_violations(dept: str, res: Any) -> List[Dict[str, Any]]:
    """Extract list of violation dicts from department result reasons."""
    violations: List[Dict[str, Any]] = []
    reasons = getattr(res, "reasons", None)
    if reasons is None and isinstance(res, dict):
        reasons = res.get("reasons", [])

    if reasons:
        for r in reasons:
            passed = getattr(r, "passed", None) if not isinstance(r, dict) else r.get("passed")
            # If explicitly failed, or if passed is omitted under a REJECTED status
            if passed is False or (passed is None and _extract_status(res) == DepartmentStatus.REJECTED):
                rule_id = getattr(r, "rule_id", None) if not isinstance(r, dict) else r.get("rule_id")
                message = getattr(r, "message", None) if not isinstance(r, dict) else r.get("message")
                violations.append({
                    "department": dept,
                    "rule_id": rule_id or "UNKNOWN",
                    "message": message or "Rule violation",
                })

    if not violations:
        # Fallback if department rejected without specific reason objects
        err_msg = getattr(res, "error_message", None) if not isinstance(res, dict) else res.get("error_message")
        violations.append({
            "department": dept,
            "rule_id": "REJECTION",
            "message": err_msg or f"{dept.capitalize()} department rejected the application.",
        })

    return violations


def evaluate_consensus(
    department_results: Dict[str, DepartmentResultSummary],
) -> Tuple[FinalDecision, DecisionCode, str, List[dict]]:
    """
    Evaluate department results and produce the final permit decision.

    Rules:
    1. Incomplete / Failed reviews take precedence: If ANY of the 4 departments
       ('zoning', 'fire', 'water', 'structural') is missing from the results map,
       or has status TIMEOUT or ERROR:
       -> returns (FinalDecision.REJECTED, DecisionCode.INCOMPLETE_REVIEWS, summary, [])
    2. Department rejections: If all 4 responded, and one or more returned
       DepartmentStatus.REJECTED:
       -> returns (FinalDecision.REJECTED, DecisionCode.DEPARTMENT_REJECTIONS, summary, list of violations)
    3. Full approval: If all 4 departments returned DepartmentStatus.APPROVED:
       -> returns (FinalDecision.APPROVED, DecisionCode.ALL_DEPARTMENTS_APPROVED, summary, [])
    """
    # Normalize dictionary keys
    normalized: Dict[str, Any] = {}
    if department_results:
        for k, v in department_results.items():
            normalized[_normalize_department_name(k)] = v

    # Rule 1: Check for missing or incomplete/failed reviews
    failing_departments: List[str] = []
    for dept in REQUIRED_DEPARTMENTS:
        if dept not in normalized or normalized[dept] is None:
            failing_departments.append(f"{dept} (missing)")
            continue

        status = _extract_status(normalized[dept])
        if status is None:
            failing_departments.append(f"{dept} (missing status)")
        elif status == DepartmentStatus.TIMEOUT:
            failing_departments.append(f"{dept} (TIMEOUT)")
        elif status == DepartmentStatus.ERROR:
            failing_departments.append(f"{dept} (ERROR)")
        elif status not in (DepartmentStatus.APPROVED, DepartmentStatus.REJECTED):
            failing_departments.append(f"{dept} ({status.value})")

    if failing_departments:
        summary = (
            f"Application rejected as incomplete: review failed or missing for department(s): "
            f"{', '.join(failing_departments)}."
        )
        return (
            FinalDecision.REJECTED,
            DecisionCode.INCOMPLETE_REVIEWS,
            summary,
            [],
        )

    # Rule 2: Check for department rejections
    rejecting_departments: List[str] = []
    violations: List[Dict[str, Any]] = []
    for dept in REQUIRED_DEPARTMENTS:
        res = normalized[dept]
        status = _extract_status(res)
        if status == DepartmentStatus.REJECTED:
            rejecting_departments.append(dept)
            violations.extend(_extract_violations(dept, res))

    if rejecting_departments:
        count = len(rejecting_departments)
        dept_str = ", ".join(d.capitalize() for d in rejecting_departments)
        summary = (
            f"Application rejected due to violations in {count} department"
            f"{'s' if count != 1 else ''}: {dept_str}."
        )
        return (
            FinalDecision.REJECTED,
            DecisionCode.DEPARTMENT_REJECTIONS,
            summary,
            violations,
        )

    # Rule 3: Full approval (all 4 departments approved)
    summary = "All 4 departments (Zoning, Fire, Water, Structural) approved the application."
    return (
        FinalDecision.APPROVED,
        DecisionCode.ALL_DEPARTMENTS_APPROVED,
        summary,
        [],
    )
