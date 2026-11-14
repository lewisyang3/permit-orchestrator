"""Unit tests for the consensus engine (orchestrator/consensus.py)."""

import pytest

from contract.models import (
    DecisionCode,
    DepartmentName,
    DepartmentResultSummary,
    DepartmentStatus,
    EvaluationReason,
    FinalDecision,
    RuleSeverity,
)
from orchestrator.consensus import REQUIRED_DEPARTMENTS, evaluate_consensus


def _make_summary(
    status: DepartmentStatus,
    reasons: list[EvaluationReason] | None = None,
    attempts: int = 1,
    latency_ms: float = 50.0,
    error_message: str | None = None,
) -> DepartmentResultSummary:
    """Helper to create a DepartmentResultSummary."""
    return DepartmentResultSummary(
        status=status,
        attempts=attempts,
        latency_ms=latency_ms,
        error_message=error_message,
        reasons=reasons or [],
    )


def _all_approved() -> dict[str, DepartmentResultSummary]:
    """Helper to return an all-approved department results dict."""
    return {
        dept: _make_summary(
            status=DepartmentStatus.APPROVED,
            reasons=[
                EvaluationReason(
                    rule_id=f"{dept.upper()}-001",
                    rule_name=f"{dept.capitalize()} Check",
                    passed=True,
                    severity=RuleSeverity.INFO,
                    message="Complies with municipal code.",
                )
            ],
        )
        for dept in REQUIRED_DEPARTMENTS
    }


# ============================================================================
# Rule 3: Full Approval Tests
# ============================================================================


def test_full_approval_all_four_departments():
    results = _all_approved()
    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.APPROVED
    assert code == DecisionCode.ALL_DEPARTMENTS_APPROVED
    assert "approved" in summary.lower()
    assert violations == []


def test_full_approval_with_department_enum_keys():
    results = {
        DepartmentName.ZONING: _make_summary(DepartmentStatus.APPROVED),
        DepartmentName.FIRE: _make_summary(DepartmentStatus.APPROVED),
        DepartmentName.WATER: _make_summary(DepartmentStatus.APPROVED),
        DepartmentName.STRUCTURAL: _make_summary(DepartmentStatus.APPROVED),
    }
    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.APPROVED
    assert code == DecisionCode.ALL_DEPARTMENTS_APPROVED
    assert violations == []


def test_full_approval_case_insensitivity():
    results = {
        "ZONING": _make_summary(DepartmentStatus.APPROVED),
        "Fire": _make_summary(DepartmentStatus.APPROVED),
        "water": _make_summary(DepartmentStatus.APPROVED),
        "STRUCTURAL": _make_summary(DepartmentStatus.APPROVED),
    }
    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.APPROVED
    assert code == DecisionCode.ALL_DEPARTMENTS_APPROVED
    assert violations == []


# ============================================================================
# Rule 1: Incomplete / Failed Reviews Take Precedence
# ============================================================================


def test_empty_results_map_rejected_incomplete():
    decision, code, summary, violations = evaluate_consensus({})

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert violations == []
    for dept in REQUIRED_DEPARTMENTS:
        assert dept in summary


def test_none_results_map_rejected_incomplete():
    decision, code, summary, violations = evaluate_consensus(None)  # type: ignore

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert violations == []


def test_single_missing_department():
    results = _all_approved()
    del results["fire"]

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "fire" in summary
    assert violations == []


def test_explicit_none_department_value():
    results = _all_approved()
    results["water"] = None  # type: ignore

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "water" in summary
    assert violations == []


def test_single_department_timeout():
    results = _all_approved()
    results["fire"] = _make_summary(DepartmentStatus.TIMEOUT, attempts=3, latency_ms=6000.0)

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "fire" in summary
    assert "TIMEOUT" in summary
    assert violations == []


def test_single_department_error():
    results = _all_approved()
    results["structural"] = _make_summary(
        DepartmentStatus.ERROR,
        attempts=3,
        error_message="HTTP 500 internal server error",
    )

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "structural" in summary
    assert "ERROR" in summary
    assert violations == []


def test_single_department_pending():
    results = _all_approved()
    results["zoning"] = _make_summary(DepartmentStatus.PENDING)

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "zoning" in summary
    assert violations == []


def test_multiple_failing_departments():
    results = _all_approved()
    results["fire"] = _make_summary(DepartmentStatus.TIMEOUT)
    results["water"] = _make_summary(DepartmentStatus.ERROR)
    del results["structural"]

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "fire" in summary
    assert "water" in summary
    assert "structural" in summary
    assert violations == []


def test_incomplete_reviews_precedence_over_rejection():
    """
    Rule 1 precedence: Even if a department has validly rejected the permit,
    if any department timed out, errored, or is missing, the decision MUST be
    INCOMPLETE_REVIEWS, never DEPARTMENT_REJECTIONS.
    """
    results = _all_approved()
    # Zoning rejects with clear violations
    results["zoning"] = _make_summary(
        DepartmentStatus.REJECTED,
        reasons=[
            EvaluationReason(
                rule_id="ZONE-001",
                rule_name="Height Limit",
                passed=False,
                severity=RuleSeverity.VIOLATION,
                message="Building height 45ft exceeds 35ft max.",
            )
        ],
    )
    # Fire timed out
    results["fire"] = _make_summary(DepartmentStatus.TIMEOUT, attempts=3)

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "fire" in summary
    assert violations == []


def test_incomplete_reviews_precedence_over_missing_and_error_with_rejections():
    results = {
        "zoning": _make_summary(DepartmentStatus.REJECTED),
        "fire": _make_summary(DepartmentStatus.REJECTED),
        "water": _make_summary(DepartmentStatus.ERROR),
        # structural is completely missing
    }
    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.INCOMPLETE_REVIEWS
    assert "water" in summary
    assert "structural" in summary
    assert violations == []


# ============================================================================
# Rule 2: Department Rejections Tests
# ============================================================================


def test_single_department_rejection_extracts_violations():
    results = _all_approved()
    results["fire"] = _make_summary(
        DepartmentStatus.REJECTED,
        reasons=[
            EvaluationReason(
                rule_id="FIRE-001",
                rule_name="Sprinkler Requirement",
                passed=False,
                severity=RuleSeverity.VIOLATION,
                message="Automatic sprinkler system required for assembly occupancy over 100 occupants.",
            ),
            EvaluationReason(
                rule_id="FIRE-002",
                rule_name="Egress Exits",
                passed=False,
                severity=RuleSeverity.VIOLATION,
                message="Minimum 2 egress exits required for occupant load 110 (1 provided).",
            ),
            # Also include a passed rule to ensure it's filtered out
            EvaluationReason(
                rule_id="FIRE-003",
                rule_name="Hydrant Distance",
                passed=True,
                severity=RuleSeverity.INFO,
                message="Hydrant within 250ft.",
            ),
        ],
    )

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.DEPARTMENT_REJECTIONS
    assert "1 department" in summary
    assert "Fire" in summary

    assert len(violations) == 2
    assert violations[0] == {
        "department": "fire",
        "rule_id": "FIRE-001",
        "message": "Automatic sprinkler system required for assembly occupancy over 100 occupants.",
    }
    assert violations[1] == {
        "department": "fire",
        "rule_id": "FIRE-002",
        "message": "Minimum 2 egress exits required for occupant load 110 (1 provided).",
    }


def test_multiple_department_rejections_aggregate_violations():
    results = _all_approved()
    results["zoning"] = _make_summary(
        DepartmentStatus.REJECTED,
        reasons=[
            EvaluationReason(
                rule_id="ZONE-101",
                rule_name="Setback",
                passed=False,
                severity=RuleSeverity.VIOLATION,
                message="Front setback 10ft is less than required 15ft.",
            )
        ],
    )
    results["water"] = _make_summary(
        DepartmentStatus.REJECTED,
        reasons=[
            EvaluationReason(
                rule_id="WATER-201",
                rule_name="Backflow Preventer",
                passed=False,
                severity=RuleSeverity.VIOLATION,
                message="Commercial project requires RPZ backflow preventer.",
            )
        ],
    )

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.DEPARTMENT_REJECTIONS
    assert "2 departments" in summary
    assert "Zoning" in summary
    assert "Water" in summary

    assert len(violations) == 2
    assert violations[0]["department"] == "zoning"
    assert violations[0]["rule_id"] == "ZONE-101"
    assert violations[1]["department"] == "water"
    assert violations[1]["rule_id"] == "WATER-201"


def test_all_four_departments_rejected():
    results = {
        dept: _make_summary(
            DepartmentStatus.REJECTED,
            reasons=[
                EvaluationReason(
                    rule_id=f"{dept.upper()}-ERR",
                    rule_name=f"{dept.capitalize()} Error",
                    passed=False,
                    severity=RuleSeverity.VIOLATION,
                    message=f"{dept.capitalize()} non-compliant.",
                )
            ],
        )
        for dept in REQUIRED_DEPARTMENTS
    }

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.DEPARTMENT_REJECTIONS
    assert "4 departments" in summary
    assert len(violations) == 4


def test_rejection_without_reasons_provides_fallback_violation():
    results = _all_approved()
    results["structural"] = _make_summary(
        DepartmentStatus.REJECTED,
        reasons=[],
        error_message="Foundation type unsuited for seismic category D.",
    )

    decision, code, summary, violations = evaluate_consensus(results)

    assert decision == FinalDecision.REJECTED
    assert code == DecisionCode.DEPARTMENT_REJECTIONS
    assert len(violations) == 1
    assert violations[0]["department"] == "structural"
    assert violations[0]["rule_id"] == "REJECTION"
    assert "Foundation type unsuited" in violations[0]["message"]
