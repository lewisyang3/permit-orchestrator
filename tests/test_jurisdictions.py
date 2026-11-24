"""Per-city rule pack registry and department routing."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from contract.models import DepartmentName, DepartmentStatus, Permit, PropertyAddress
from contract.normalize import normalize_city
from departments.app import create_app as department_app
from departments.jurisdictions import load_registry
from orchestrator.dispatcher import dispatch_permit

RULES = Path("rules")
SAN_JOSE = RULES / "ca-san-jose"


def _addr(city: str, state: str = "CA") -> PropertyAddress:
    return PropertyAddress(street="1 Test St", city=city, state=state, zip="95125")


@pytest.fixture
def permit_dict() -> Dict[str, Any]:
    return json.loads(Path("contract/fixtures/permit_approved.json").read_text())


@pytest.fixture
def rules_copy(tmp_path: Path) -> Path:
    root = tmp_path / "rules"
    shutil.copytree(SAN_JOSE, root / "ca-san-jose")
    return root


def _edit_yaml(path: Path, **changes: Any) -> None:
    data = yaml.safe_load(path.read_text())
    data.update(changes)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


# --- normalization and resolution ------------------------------------------

@pytest.mark.parametrize("raw", ["San José", "San Jose", "SAN JOSE", " san  jose. ", "San-Jose"])
def test_city_spellings_normalize_together(raw):
    assert normalize_city(raw) == "san jose"


@pytest.mark.parametrize("dept", list(DepartmentName))
def test_registry_loads_san_jose_for_every_department(dept):
    registry = load_registry(RULES, dept)
    assert list(registry.jurisdictions) == ["ca-san-jose"]
    assert registry.jurisdictions["ca-san-jose"].pack["department"] == dept.value


@pytest.mark.parametrize(
    "city,state",
    [("San José", "CA"), ("San Jose", "CA"), ("san jose", "California"), ("SAN JOSE", "ca")],
)
def test_resolves_san_jose_spellings(city, state):
    registry = load_registry(RULES, DepartmentName.ZONING)
    assert registry.resolve(_addr(city, state)).jurisdiction_id == "ca-san-jose"


@pytest.mark.parametrize(
    "city,state",
    [("Sunnyvale", "CA"), ("San Jose", "NM"), ("Springfield", "OR"), ("San Jose", "Atlantis")],
)
def test_unknown_city_or_state_does_not_resolve(city, state):
    registry = load_registry(RULES, DepartmentName.ZONING)
    assert registry.resolve(_addr(city, state)) is None


def test_second_city_is_routed_independently(rules_copy: Path):
    sv = rules_copy / "ca-sunnyvale"
    shutil.copytree(rules_copy / "ca-san-jose", sv)
    _edit_yaml(sv / "city.yaml", jurisdiction_id="ca-sunnyvale", city="Sunnyvale", aliases=[])
    _edit_yaml(sv / "fire.yaml", jurisdiction_id="ca-sunnyvale", rule_set_id="sunnyvale-fire-test")
    _edit_yaml(sv / "city.yaml", departments=["fire"])
    for dept in ("zoning", "water", "structural"):
        (sv / f"{dept}.yaml").unlink()

    fire = load_registry(rules_copy, DepartmentName.FIRE)
    assert fire.resolve(_addr("Sunnyvale")).pack["rule_set_id"] == "sunnyvale-fire-test"
    assert fire.resolve(_addr("San Jose")).jurisdiction_id == "ca-san-jose"
    # Sunnyvale has no zoning pack, so zoning only covers San José.
    zoning = load_registry(rules_copy, DepartmentName.ZONING)
    assert zoning.resolve(_addr("Sunnyvale")) is None


# --- fail-fast validation ---------------------------------------------------

def test_folder_and_city_id_must_match(rules_copy: Path):
    _edit_yaml(rules_copy / "ca-san-jose" / "city.yaml", jurisdiction_id="ca-somewhere-else")
    with pytest.raises(ValueError, match="must match its folder"):
        load_registry(rules_copy, DepartmentName.FIRE)


def test_pack_jurisdiction_must_match_city(rules_copy: Path):
    _edit_yaml(rules_copy / "ca-san-jose" / "fire.yaml", jurisdiction_id="ca-sunnyvale")
    with pytest.raises(ValueError, match="jurisdiction_id 'ca-sunnyvale' must be 'ca-san-jose'"):
        load_registry(rules_copy, DepartmentName.FIRE)


def test_listed_department_needs_its_pack(rules_copy: Path):
    (rules_copy / "ca-san-jose" / "water.yaml").unlink()
    with pytest.raises(ValueError, match="water.yaml is missing"):
        load_registry(rules_copy, DepartmentName.WATER)


def test_two_cities_cannot_claim_the_same_name(rules_copy: Path):
    dup = rules_copy / "ca-san-jose-copy"
    shutil.copytree(rules_copy / "ca-san-jose", dup)
    _edit_yaml(dup / "city.yaml", jurisdiction_id="ca-san-jose-copy")
    for f in dup.glob("*.yaml"):
        if f.name != "city.yaml":
            _edit_yaml(f, jurisdiction_id="ca-san-jose-copy")
    with pytest.raises(ValueError, match="claimed by both"):
        load_registry(rules_copy, DepartmentName.FIRE)


def test_no_packs_for_department_fails(tmp_path: Path):
    (tmp_path / "rules").mkdir()
    with pytest.raises(ValueError, match="No city rule packs"):
        load_registry(tmp_path / "rules", DepartmentName.FIRE)


# --- department service -----------------------------------------------------

def _evaluate(client: TestClient, permit: Dict[str, Any], dept: str = "zoning") -> httpx.Response:
    return client.post(
        "/evaluate",
        json={"permit_id": "pm-1", "trace_id": "tr-1", "department": dept, "permit": permit},
    )


def test_san_jose_permit_is_evaluated_against_san_jose_pack(permit_dict):
    with TestClient(department_app("zoning", rules_root=RULES)) as client:
        resp = _evaluate(client, permit_dict)
    body = resp.json()
    if resp.status_code == 200:
        assert body["jurisdiction_id"] == "ca-san-jose"
    else:
        assert body["detail"]["code"] == "INCOMPLETE_REVIEW"
        assert body["detail"]["jurisdiction_id"] == "ca-san-jose"
        assert body["detail"]["rule_set_id"] == "san-jose-zoning-residential-starter"


def test_other_city_is_not_evaluated_against_san_jose(permit_dict):
    permit_dict["property_address"].update(city="Sunnyvale", zip="94086")
    with TestClient(department_app("zoning", rules_root=RULES)) as client:
        resp = _evaluate(client, permit_dict)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "UNSUPPORTED_JURISDICTION"
    assert detail["findings"] == []
    # Other customers' cities are never listed.
    assert "supported_jurisdictions" not in detail
    assert "San Jos" not in resp.text
    [reason] = detail["unresolved_reasons"]
    assert reason["rule_id"] == "UNSUPPORTED_JURISDICTION"
    assert "Sunnyvale" in reason["message"]


def test_rules_endpoint_lists_jurisdictions():
    with TestClient(department_app("fire", rules_root=RULES)) as client:
        data = client.get("/rules").json()
    [sj] = data["jurisdictions"]
    assert sj["jurisdiction_id"] == "ca-san-jose"
    assert sj["rule_set_id"] == "san-jose-fire-residential-starter"


# --- orchestrator sees why ----------------------------------------------------

def test_dispatcher_keeps_unsupported_jurisdiction_detail(permit_dict):
    permit_dict["property_address"].update(city="Sunnyvale")
    permit = Permit.model_validate(permit_dict)
    dept_apps = {d.value: department_app(d.value, rules_root=RULES) for d in DepartmentName}

    async def _run():
        clients = {name: TestClient(app) for name, app in dept_apps.items()}
        for c in clients.values():
            c.__enter__()
        try:
            def handler(request: httpx.Request) -> httpx.Response:
                dept = json.loads(request.content)["department"]
                r = clients[dept].post("/evaluate", content=request.content,
                                       headers={"content-type": "application/json"})
                return httpx.Response(r.status_code, content=r.content, headers=r.headers)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await dispatch_permit(permit=permit, client=client)
        finally:
            for c in clients.values():
                c.__exit__(None, None, None)

    results = asyncio.run(_run())
    for dept, summary in results.items():
        assert summary.status == DepartmentStatus.ERROR, dept
        assert "UNSUPPORTED_JURISDICTION" in summary.error_message
        assert [r.rule_id for r in summary.reasons] == ["UNSUPPORTED_JURISDICTION"]
