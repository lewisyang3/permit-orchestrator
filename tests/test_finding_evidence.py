"""Department findings carry the plan evidence and city sources behind each rule."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from fastapi.testclient import TestClient

from contract.models import DepartmentName, Permit
from departments.app import create_app as department_app
from departments.evaluator import (
    FACT_TO_PERMIT_PATH,
    evaluate_department,
    load_and_validate_rule_pack,
    map_permit_to_facts,
    rule_evidence_paths,
)
from orchestrator.handlers.intake import parse_plan_document

SAMPLE_PDF = Path("fixtures/sample_plans/residential_rear_addition.pdf").read_bytes()
SOURCE = {
    "url": "https://example.org/city-guide.pdf",
    "section": "Front setbacks, Table 1",
    "printed_page": 19,
    "checked_on": "2026-11-14",
}


@pytest.fixture(scope="module")
def plan_permit() -> Permit:
    """The sample plan as intake parses it: every value cited and verified (front setback 25 ft)."""
    permit, _, _, _ = asyncio.run(
        parse_plan_document("plan.pdf", base64.b64encode(SAMPLE_PDF).decode(), save_upload=False)
    )
    assert permit.evidence["zoning.setback_front_ft"].status == "verified"
    return permit


def _pack(checks: List[Dict[str, Any]], when: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return load_and_validate_rule_pack({
        "schema_version": "0.2.0",
        "rule_set_id": "test-zoning",
        "version": "0.2.0",
        "department": "zoning",
        "evaluation_policy": {"allow_final_department_approval_from_this_pack_alone": False},
        "rules": [{
            "rule_id": "TEST-Z-1",
            "rule_name": "Front setback",
            "applies_when": [],
            "requirement": {"cases": [{"when": when or [], "checks": checks}]},
            "source": [SOURCE],
        }],
    })


FRONT_30 = [{"field": "front_setback_ft", "operator": "gte", "value": 30, "unit": "ft"}]


def test_every_permit_backed_fact_maps_to_a_real_permit_path(plan_permit):
    facts = map_permit_to_facts(plan_permit)
    leaves = set(plan_permit.evidence)
    for fact, path in FACT_TO_PERMIT_PATH.items():
        assert fact in facts, fact
        assert path in leaves, path


def test_rule_paths_cover_conditions_and_checks():
    rule = _pack(FRONT_30, when=[{"field": "zone_code", "operator": "eq", "value": "R-1"}])["rules"][0]
    assert rule_evidence_paths(rule) == ["zoning.zone_code", "zoning.setback_front_ft"]


def test_failing_reason_cites_the_plan_and_the_city_source(plan_permit):
    report = evaluate_department(_pack(FRONT_30), permit=plan_permit)
    assert report.is_rejected
    [reason] = report.reasons
    [ev] = reason.evidence
    assert ev.field_path == "zoning.setback_front_ft"
    assert (ev.quote, ev.page, ev.status) == ("Front Setback: 25.0 ft", 1, "verified")
    assert ev.bboxes
    assert reason.rule_sources == [SOURCE]
    assert "unverified" not in reason.message


def test_fail_on_unverified_value_warns_before_rejecting(plan_permit):
    permit = plan_permit.model_copy(deep=True)
    permit.evidence["zoning.setback_front_ft"].status = "unverified"
    report = evaluate_department(_pack(FRONT_30), permit=permit)
    [reason] = report.reasons
    assert reason.message.endswith(
        "(Source text for zoning.setback_front_ft is unverified; confirm it in the plan before rejecting.)"
    )
    # The caution survives an explanation rewrite.
    report.apply_explanations({"TEST-Z-1": "Setback is 25 ft; 30 ft required."})
    assert report.reasons[0].message.startswith("Setback is 25 ft; 30 ft required. (Source text for")


def test_passing_reason_never_gets_the_caution(plan_permit):
    permit = plan_permit.model_copy(deep=True)
    permit.evidence["zoning.setback_front_ft"].status = "unverified"
    pack = _pack([{"field": "front_setback_ft", "operator": "gte", "value": 20, "unit": "ft"}])
    report = evaluate_department(pack, permit=permit)
    assert all("unverified" not in r.message for r in report.reasons)
    assert report.reasons[0].evidence[0].status == "unverified"


def test_permit_without_evidence_still_evaluates(plan_permit):
    permit = plan_permit.model_copy(deep=True, update={"evidence": {}})
    [reason] = evaluate_department(_pack(FRONT_30), permit=permit).reasons
    assert reason.evidence == [] and reason.rule_sources == [SOURCE]


def test_department_service_returns_evidence_over_http(tmp_path, plan_permit):
    rule_file = tmp_path / "zoning.yaml"
    pack = _pack(FRONT_30)
    rule_file.write_text(yaml.safe_dump(pack))
    with TestClient(department_app("zoning", rule_file=rule_file)) as client:
        resp = client.post("/evaluate", json={
            "permit_id": "pm-1", "trace_id": "tr-1", "department": "zoning",
            "permit": plan_permit.model_dump(mode="json"),
        })
    assert resp.status_code == 200, resp.text
    [reason] = resp.json()["reasons"]
    assert reason["evidence"][0]["quote"] == "Front Setback: 25.0 ft"
    assert reason["rule_sources"][0]["printed_page"] == 19


def test_san_jose_pack_reasons_cite_their_sources(plan_permit):
    pack = load_and_validate_rule_pack("rules/ca-san-jose/zoning.yaml", DepartmentName.ZONING)
    report = evaluate_department(pack, permit=plan_permit)
    for reason in report.unresolved_reasons:
        assert reason.rule_sources, reason.rule_id
        assert all("url" in s for s in reason.rule_sources)


def test_trace_events_carry_findings_with_evidence(plan_permit, tmp_path):
    """The UI reads findings from department trace events, so evidence must survive dispatch."""
    import json as _json

    import httpx

    from contract.models import TraceEventType
    from orchestrator.dispatcher import dispatch_permit

    rule_file = tmp_path / "zoning.yaml"
    rule_file.write_text(yaml.safe_dump(_pack(FRONT_30)))
    apps = {"zoning": department_app("zoning", rule_file=rule_file)}
    # Real San José packs for the rest; the sample plan is in San José, so they resolve.
    for dept in ("fire", "water", "structural"):
        apps[dept] = department_app(dept, rules_root=Path("rules"))

    events = []

    async def _run():
        clients = {name: TestClient(app) for name, app in apps.items()}
        for c in clients.values():
            c.__enter__()
        try:
            def handler(request: httpx.Request) -> httpx.Response:
                dept = _json.loads(request.content)["department"]
                r = clients[dept].post("/evaluate", content=request.content,
                                       headers={"content-type": "application/json"})
                return httpx.Response(r.status_code, content=r.content, headers=r.headers)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await dispatch_permit(permit=plan_permit, client=client, on_event=events.append)
        finally:
            for c in clients.values():
                c.__exit__(None, None, None)

    results = asyncio.run(_run())
    assert results["zoning"].status.value == "REJECTED"

    zoning_ok = [e for e in events if e.span.value == "dept:zoning" and e.type == TraceEventType.CALL_SUCCEEDED]
    [finding] = zoning_ok[0].detail["findings"]
    assert finding["department"] == "zoning" and finding["rule_id"] == "TEST-Z-1"
    assert finding["evidence"][0]["quote"] == "Front Setback: 25.0 ft"
    assert finding["rule_sources"] == [SOURCE]

    # Incomplete San José reviews come back as 422s; their reasons still reach the trace.
    fire_failed = [e for e in events if e.span.value == "dept:fire" and e.type == TraceEventType.CALL_FAILED]
    assert fire_failed and fire_failed[0].detail["findings"]
    assert all(f["rule_sources"] for f in fire_failed[0].detail["findings"])
