"""Verify scaffold isolation, fail-safe HTTP behavior, and SQLite persistence."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from departments.app import create_app as department_app
from dev.orchestrator import create_app as harness_app


def permit():
    return json.loads(Path("contract/fixtures/permit_approved.json").read_text())


@pytest.mark.parametrize("name,count", [("zoning", 5), ("fire", 6), ("water", 7), ("structural", 6)])
def test_each_department_loads_its_own_rules_and_does_not_fake_review(name, count):
    with TestClient(department_app(name)) as client:
        assert client.get("/health").json() == {"status": "healthy", "department": name}
        assert client.get("/rules").json()["rule_count"] == count
        response = client.post("/evaluate", json={
            "permit_id": "test-permit", "trace_id": "test-trace",
            "department": name, "attempt": 1, "permit": permit(),
        })
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INCOMPLETE_REVIEW"


def test_department_refuses_wrong_target_and_rule_pack():
    with TestClient(department_app("fire")) as client:
        response = client.post("/evaluate", json={
            "permit_id": "test-permit", "trace_id": "test-trace",
            "department": "water", "permit": permit(),
        })
        assert response.status_code == 422
    with pytest.raises(ValueError, match="does not match"):
        with TestClient(department_app("fire", Path("rules/water.yaml"))):
            pass


def test_fanout_errors_are_saved_and_survive_reopening_database(tmp_path):
    calls = []

    def unavailable(request):
        calls.append((request.url.host, json.loads(request.content)["department"]))
        return httpx.Response(501)

    path = tmp_path / "data" / "permits.db"
    with TestClient(harness_app(path, httpx.MockTransport(unavailable))) as client:
        assert client.get("/health").status_code == 200
        result = client.post("/demo/submit", json=permit()).json()
        assert result["final_decision"] == "REJECTED"
        assert result["decision_code"] == "INCOMPLETE_REVIEWS"
        assert {entry["status"] for entry in result["department_results"].values()} == {"ERROR"}
        assert sorted(calls) == sorted((name, name) for name in ("zoning", "fire", "water", "structural"))
    with TestClient(harness_app(path, httpx.MockTransport(unavailable))) as client:
        assert client.get(f"/demo/permits/{result['permit_id']}").json() == result
        assert client.get("/demo/permits/missing").status_code == 404


@pytest.mark.parametrize("fault", ["timeout", "wrong_identity", "nonterminal", "missing_result"])
def test_fire_failure_cannot_produce_approval(tmp_path, fault):
    def respond(request):
        body = json.loads(request.content)
        data = {"permit_id": body["permit_id"], "department": body["department"],
                "status": "APPROVED", "execution_time_ms": 1, "reasons": []}
        if body["department"] == "fire":
            if fault == "timeout":
                raise httpx.ReadTimeout("Fire unavailable", request=request)
            if fault == "wrong_identity":
                data["permit_id"] = "another-permit"
            if fault == "nonterminal":
                data["status"] = "PENDING"
            if fault == "missing_result":
                data = {}
        return httpx.Response(200, json=data)

    with TestClient(harness_app(tmp_path / "permits.db", httpx.MockTransport(respond))) as client:
        result = client.post("/demo/submit", json=permit()).json()
        assert result["final_decision"] == "REJECTED"
        assert result["decision_code"] == "INCOMPLETE_REVIEWS"
        assert result["department_results"]["fire"]["status"] in ("TIMEOUT", "ERROR")


def test_complete_mock_consensus(tmp_path):
    def approve(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "permit_id": body["permit_id"], "department": body["department"],
            "status": "APPROVED", "execution_time_ms": 1, "reasons": [],
        })

    with TestClient(harness_app(tmp_path / "permits.db", httpx.MockTransport(approve))) as client:
        result = client.post("/demo/submit", json=permit()).json()
        assert result["final_decision"] == "APPROVED"
        assert len(result["department_results"]) == 4


def test_department_fault_injection_modes():
    with TestClient(department_app("fire")) as client:
        # Default fault mode is none
        resp = client.get("/fault")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "none"

        eval_payload = {
            "permit_id": "test-permit",
            "trace_id": "test-trace",
            "department": "fire",
            "attempt": 1,
            "permit": permit(),
        }

        # Offline -> 503
        set_off = client.post("/fault", json={"mode": "offline"})
        assert set_off.status_code == 200
        assert client.get("/fault").json()["mode"] == "offline"
        eval_off = client.post("/evaluate", json=eval_payload)
        assert eval_off.status_code == 503
        assert "offline" in eval_off.json()["detail"].lower()

        # Error -> 500
        set_err = client.post("/fault", json={"mode": "error", "error_code": 500, "error_message": "Fault injection"})
        assert set_err.status_code == 200
        eval_err = client.post("/evaluate", json=eval_payload)
        assert eval_err.status_code == 500
        assert "Fault injection" in eval_err.json()["detail"]

        # Reset to none
        set_none = client.post("/fault", json={"mode": "none"})
        assert set_none.status_code == 200
        assert client.get("/fault").json()["mode"] == "none"
        eval_none = client.post("/evaluate", json=eval_payload)
        assert eval_none.status_code == 501


def test_department_cors_middleware():
    with TestClient(department_app("zoning")) as client:
        cors = client.options(
            "/evaluate",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
        )
        assert cors.status_code == 200
        assert cors.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert "POST" in cors.headers.get("access-control-allow-methods", "")


def test_compose_wiring_and_orchestrator_dockerfile():
    import yaml
    from orchestrator.app import app as prod_orchestrator_app

    compose_file = Path("compose.yaml")
    assert compose_file.exists()
    cfg = yaml.safe_load(compose_file.read_text())

    # 1. Orchestrator points to orchestrator/Dockerfile
    orch = cfg["services"]["orchestrator"]
    assert orch["build"]["dockerfile"] == "orchestrator/Dockerfile"
    assert "8000:8000" in orch["ports"]

    # 2. Check orchestrator/Dockerfile content
    dockerfile = Path("orchestrator/Dockerfile")
    assert dockerfile.exists()
    content = dockerfile.read_text()
    assert "orchestrator.app:app" in content
    assert "COPY orchestrator ./orchestrator" in content

    # 3. Check department port mappings for dual-stack localhost accessibility
    for dept, expected_port in [("zoning", 8001), ("fire", 8002), ("water", 8003), ("structural", 8004)]:
        assert f"{expected_port}:{expected_port}" in cfg["services"][dept]["ports"]

    # 4. Production orchestrator app health
    with TestClient(prod_orchestrator_app) as client:
        health_resp = client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "healthy"}
