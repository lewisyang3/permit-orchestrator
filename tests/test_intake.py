"""Test suite for Task T13: Intake Plan and PDF Parsing."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from contract.models import (
    Applicant,
    ApplicantRole,
    ConstructionType,
    FoundationType,
    OccupancyType,
    Permit,
    ZoneCode,
)
from orchestrator.app import create_app
from orchestrator.handlers.intake import (
    InvalidFieldsError,
    MissingFieldsError,
    deterministic_plan_parse,
    parse_plan_document,
    validate_extracted_permit_dict,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app_client():
    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sample_pdf_bytes():
    pdf_path = Path("fixtures/sample_plans/residential_rear_addition.pdf")
    if pdf_path.exists():
        return pdf_path.read_bytes()
    return b"%PDF-1.4\n%trailer\n%%EOF"


@pytest.fixture
def incomplete_pdf_bytes():
    pdf_path = Path("fixtures/sample_plans/incomplete_plan.pdf")
    if pdf_path.exists():
        return pdf_path.read_bytes()
    return b"%PDF-1.4\n1 0 obj\n(Project Title: Minimal) Tj\nendobj\n%%EOF"


@pytest.fixture
def sample_plan_text():
    plan_path = Path("fixtures/sample_plans/residential_rear_addition.txt")
    return plan_path.read_text(encoding="utf-8")


def test_deterministic_text_plan_parsing(sample_plan_text):
    """Test that text architectural plan is parsed into a fully compliant Permit model."""
    permit, warnings = deterministic_plan_parse(
        content_text=sample_plan_text,
        filename="residential_rear_addition_plan.txt",
    )

    assert isinstance(permit, Permit)
    assert permit.project_title == "Single Family Residential Rear Addition"
    assert permit.property_address.street == "742 Evergreen Terrace"
    assert permit.property_address.city == "San Jose"
    assert permit.property_address.zip == "95125"
    assert permit.applicant.name == "Homer Simpson"
    assert permit.applicant.role.value == "homeowner"
    assert permit.zoning.zone_code == ZoneCode.R1
    assert permit.zoning.building_height_ft == 16.5
    assert permit.fire.occupancy_type == OccupancyType.RESIDENTIAL
    assert permit.fire.has_sprinkler_system is False
    assert permit.fire.egress_exits_count == 2
    assert permit.water.water_meter_size_inches == 0.75
    assert permit.structural.construction_type == ConstructionType.WOOD_FRAME
    assert permit.structural.stories_count == 1


def test_deterministic_pdf_plan_parsing(sample_pdf_bytes):
    """Test deterministic fallback extraction from raw PDF stream tokens."""
    permit, warnings = deterministic_plan_parse(
        content_text=None,
        filename="residential_rear_addition.pdf",
        content_bytes=sample_pdf_bytes,
    )

    assert isinstance(permit, Permit)
    assert "Residential Rear Addition" in permit.project_title
    assert permit.zoning.zone_code == ZoneCode.R1
    assert permit.fire.occupancy_type == OccupancyType.RESIDENTIAL


def test_deterministic_parsing_raises_missing_fields_on_incomplete_plan():
    """Test that missing mandatory fields raise MissingFieldsError."""
    incomplete_text = """
    Project Title: Incomplete Porch
    Property Location:
    Address: 123 Main St
    """
    with pytest.raises(MissingFieldsError) as exc_info:
        deterministic_plan_parse(
            content_text=incomplete_text,
            filename="incomplete.txt",
        )

    err = exc_info.value
    assert "applicant.name" in err.missing_fields
    assert "applicant.email" in err.missing_fields
    assert "zoning.zone_code" in err.missing_fields
    assert "fire.occupancy_type" in err.missing_fields
    assert "structural.construction_type" in err.missing_fields


@pytest.mark.anyio
async def test_parse_plan_document_saves_to_uploads(sample_plan_text, tmp_path):
    """Test that parsed documents are archived on local disk under uploads/ per spec row 80."""
    with patch("orchestrator.handlers.intake.UPLOADS_DIR", tmp_path):
        permit, extracted_by, warnings, file_path = await parse_plan_document(
            filename="my_blueprint.txt",
            content_text=sample_plan_text,
            save_upload=True,
        )

        assert extracted_by == "deterministic_fallback"
        assert file_path is not None
        saved_file = Path(file_path)
        assert saved_file.exists()
        assert "my_blueprint.txt" in saved_file.name
        assert saved_file.read_text(encoding="utf-8") == sample_plan_text


@pytest.mark.anyio
async def test_gemini_multimodal_extraction_mock(sample_pdf_bytes):
    """Test multimodal Gemini extraction when GEMINI_API_KEY is configured."""
    mock_gemini_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": """{
                                "project_title": "Commercial Restaurant Expansion",
                                "project_description": "New dining patio",
                                "estimated_cost_usd": 150000.0,
                                "property_address": {
                                    "street": "123 Market St",
                                    "city": "San Francisco",
                                    "state": "CA",
                                    "zip": "94103"
                                },
                                "applicant": {
                                    "name": "Alice Builder",
                                    "role": "licensed_contractor",
                                    "email": "alice@builder.com",
                                    "phone": "555-123-4567"
                                },
                                "zoning": {
                                    "zone_code": "C-1",
                                    "building_height_ft": 22.0,
                                    "lot_area_sqft": 10000.0,
                                    "building_footprint_sqft": 4500.0,
                                    "setback_front_ft": 15.0,
                                    "setback_rear_ft": 20.0,
                                    "setback_side_ft": 5.0
                                },
                                "fire": {
                                    "occupancy_type": "commercial",
                                    "occupant_load": 120,
                                    "has_sprinkler_system": false,
                                    "egress_exits_count": 2,
                                    "distance_to_hydrant_ft": 80.0,
                                    "has_fire_alarm": true
                                },
                                "water": {
                                    "water_meter_size_inches": 1.5,
                                    "daily_water_usage_gpd": 1200.0,
                                    "has_backflow_preventer": true,
                                    "has_stormwater_plan": true
                                },
                                "structural": {
                                    "stories_count": 1,
                                    "construction_type": "light_steel",
                                    "seismic_design_category": "D",
                                    "foundation_type": "slab_on_grade"
                                }
                            }"""
                        }
                    ]
                }
            }
        ]
    }

    b64_content = base64.b64encode(sample_pdf_bytes).decode("ascii")

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_gemini_payload
            mock_post.return_value = mock_resp

            permit, extracted_by, warnings, file_path = await parse_plan_document(
                filename="commercial_plan.pdf",
                content_base64=b64_content,
                mime_type="application/pdf",
                save_upload=False,
            )

            assert "flash" in extracted_by
            assert permit.project_title == "Commercial Restaurant Expansion"
            assert permit.zoning.zone_code == ZoneCode.C1
            assert permit.structural.construction_type == ConstructionType.LIGHT_STEEL
            # This mock response cites no evidence, so the only warning says so.
            assert len(warnings) == 1
            assert warnings[0].startswith("No source text was cited for: project_title")
            assert permit.evidence == {}


@pytest.mark.anyio
async def test_gemini_missing_fields_raises_error():
    """Test that if Gemini response is missing required fields, MissingFieldsError is raised."""
    mock_gemini_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": """{
                                "project_title": "Incomplete Restaurant",
                                "property_address": {
                                    "street": "123 Market St",
                                    "city": "San Francisco",
                                    "state": "CA",
                                    "zip": "94103"
                                },
                                "applicant": {
                                    "name": "Alice Builder",
                                    "role": null,
                                    "email": null
                                },
                                "zoning": null,
                                "fire": null,
                                "water": null,
                                "structural": null,
                                "missing_fields": ["applicant.role", "applicant.email", "zoning", "fire", "water", "structural"]
                            }"""
                        }
                    ]
                }
            }
        ]
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_gemini_payload
            mock_post.return_value = mock_resp

            with pytest.raises(MissingFieldsError) as exc_info:
                await parse_plan_document(
                    filename="incomplete.pdf",
                    content_base64="dummy-b64",
                    mime_type="application/pdf",
                    save_upload=False,
                )

            assert "applicant.role" in exc_info.value.missing_fields
            assert "zoning.zone_code" in exc_info.value.missing_fields


@pytest.mark.anyio
async def test_gemini_failure_falls_back_to_deterministic(sample_plan_text):
    """Test that if Gemini call fails or times out, it gracefully falls back to deterministic parser."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}):
        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Read timeout")):
            permit, extracted_by, warnings, _ = await parse_plan_document(
                filename="warehouse_plan.txt",
                content_text=sample_plan_text,
                save_upload=False,
            )

            assert extracted_by == "deterministic_fallback"
            assert permit.project_title == "Single Family Residential Rear Addition"
            assert any("Gemini API extraction failed" in w for w in warnings)


def test_intake_parse_endpoint_with_text(app_client, sample_plan_text):
    """Test POST /intake/parse endpoint with text content."""
    res = app_client.post(
        "/intake/parse",
        json={
            "filename": "residential_addition.txt",
            "content_text": sample_plan_text,
            "mime_type": "text/plain",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "residential_addition.txt"
    assert "flash" in data["extracted_by"] or data["extracted_by"] == "deterministic_fallback"
    assert data["permit"]["project_title"] == "Single Family Residential Rear Addition"
    assert data["permit"]["zoning"]["zone_code"] == "R-1"


def test_intake_parse_endpoint_with_base64_pdf(app_client, sample_pdf_bytes):
    """Test POST /intake/parse endpoint with base64 PDF."""
    b64_pdf = base64.b64encode(sample_pdf_bytes).decode("ascii")
    res = app_client.post(
        "/intake/parse",
        json={
            "filename": "residential_addition.pdf",
            "content_base64": b64_pdf,
            "mime_type": "application/pdf",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "residential_addition.pdf"
    assert "permit" in data
    assert data["permit"]["permit_id"].startswith("pm-intake-")


def test_intake_parse_endpoint_missing_fields_returns_422(app_client):
    """Test POST /intake/parse returns 422 when plan document is missing required fields."""
    incomplete_content = """
    Project Title: Incomplete Storage Shed Addition
    Property Location:
    Address: 100 Main St, Springfield, OR 97477
    Zoning Parameters:
    Zone District: R-1
    """
    res = app_client.post(
        "/intake/parse",
        json={
            "filename": "incomplete_plan.txt",
            "content_text": incomplete_content,
            "mime_type": "text/plain",
        },
    )
    assert res.status_code == 422
    err_body = res.json()["detail"]
    assert err_body["error"] == "MISSING_REQUIRED_FIELDS"
    assert "applicant.name" in err_body["missing_fields"]
    assert "applicant.email" in err_body["missing_fields"]
    assert "zoning.setback_rear_ft" in err_body["missing_fields"]


def test_intake_parse_endpoint_rejects_empty_payload(app_client):
    """Test POST /intake/parse rejects request without text or base64."""
    res = app_client.post(
        "/intake/parse",
        json={"filename": "empty.pdf"},
    )
    assert res.status_code == 400
    assert "Either 'content_base64' or 'content_text' must be provided" in res.json()["detail"]


def test_sample_plans_endpoints(app_client):
    """Test GET /intake/sample-plans and GET /intake/sample-plans/{id}."""
    res = app_client.get("/intake/sample-plans")
    assert res.status_code == 200
    plans = res.json()
    assert len(plans) >= 3
    plan_ids = [p["id"] for p in plans]
    assert "residential_addition" in plan_ids
    assert "commercial_restaurant" in plan_ids

    res_single = app_client.get("/intake/sample-plans/residential_addition")
    assert res_single.status_code == 200
    detail = res_single.json()
    assert detail["id"] == "residential_addition"
    assert "Homer Simpson" in detail["sample_text"]

    res_404 = app_client.get("/intake/sample-plans/non_existent_plan")
    assert res_404.status_code == 404


# -------------------------------------------------------------------------
# Regression tests for PR #23 review findings
# -------------------------------------------------------------------------


def _gemini_payload(extracted: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(extracted)}]}}]}


def _raise_pydantic_validation_error(**_kwargs):
    """Stand-in for Permit(...) that fails like a real model constraint would."""
    Applicant.model_validate({"name": "x", "role": "homeowner", "email": None})


@pytest.fixture
def valid_permit_dict(sample_plan_text) -> dict:
    permit, _ = deterministic_plan_parse(content_text=sample_plan_text, filename="plan.txt")
    return permit.model_dump(mode="json")


def test_zero_and_false_values_are_not_treated_as_missing(valid_permit_dict):
    """A real 0 / False must survive validation instead of being reported missing."""
    data = valid_permit_dict
    data["zoning"]["setback_side_ft"] = 0
    data["fire"]["egress_exits_count"] = 0
    data["water"].pop("has_backflow_preventer")
    data["water"]["backflow_preventer_installed"] = False
    data["water"]["backflow_preventer"] = True

    permit = validate_extracted_permit_dict(data)

    assert permit.zoning.setback_side_ft == 0.0
    assert permit.fire.egress_exits_count == 0
    assert permit.water.has_backflow_preventer is False


def test_deterministic_parser_accepts_multi_word_values(sample_plan_text):
    """Role/foundation/state written in plain English parse to the right values."""
    text = (
        sample_plan_text.replace("Role: homeowner", "Role: Licensed Contractor")
        .replace("Foundation Type: continuous_spread_footing", "Foundation Type: slab on grade")
        .replace("State: OR", "State: California")
    )

    permit, _ = deterministic_plan_parse(content_text=text, filename="plan.txt")

    assert permit.applicant.role == ApplicantRole.LICENSED_CONTRACTOR
    assert permit.structural.foundation_type == FoundationType.SLAB_ON_GRADE
    assert permit.property_address.state == "CA"


def test_unrecognized_state_is_rejected_not_truncated(valid_permit_dict):
    valid_permit_dict["property_address"]["state"] = "Atlantis"
    with pytest.raises(MissingFieldsError) as exc_info:
        validate_extracted_permit_dict(valid_permit_dict)
    assert "property_address.state" in exc_info.value.missing_fields


@pytest.mark.anyio
async def test_gemini_invalid_value_surfaces_error_without_fallback(valid_permit_dict, sample_plan_text):
    """A model validation error on Gemini output is reported, not masked by a fallback parse."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}), patch(
        "orchestrator.handlers.intake.Permit", side_effect=_raise_pydantic_validation_error
    ):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = _gemini_payload(valid_permit_dict)
            mock_post.return_value = mock_resp

            with pytest.raises(InvalidFieldsError) as exc_info:
                await parse_plan_document(
                    filename="plan.txt",
                    content_text=sample_plan_text,
                    save_upload=False,
                )

    assert any("email" in e["loc"] for e in exc_info.value.errors)
    assert mock_post.call_count == 1


def test_intake_parse_endpoint_invalid_values_returns_422(app_client, valid_permit_dict, sample_plan_text):
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}), patch(
        "orchestrator.handlers.intake.Permit", side_effect=_raise_pydantic_validation_error
    ):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock(spec=httpx.Response)
            mock_resp.status_code = 200
            mock_resp.json.return_value = _gemini_payload(valid_permit_dict)
            mock_post.return_value = mock_resp

            res = app_client.post(
                "/intake/parse",
                json={"filename": "plan.txt", "content_text": sample_plan_text},
            )

    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "INVALID_FIELD_VALUES"


@pytest.mark.anyio
async def test_gemini_timeout_falls_back_after_single_attempt(sample_plan_text):
    """A timeout must not be retried across every model candidate."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}):
        with patch("httpx.AsyncClient.post", side_effect=httpx.ReadTimeout("slow")) as mock_post:
            _, extracted_by, _, _ = await parse_plan_document(
                filename="plan.txt",
                content_text=sample_plan_text,
                save_upload=False,
            )

    assert extracted_by == "deterministic_fallback"
    assert mock_post.call_count == 1


@pytest.mark.anyio
async def test_gemini_404_still_tries_next_model(valid_permit_dict, sample_plan_text):
    not_found = AsyncMock(spec=httpx.Response)
    not_found.status_code = 404
    not_found.text = "model not found"
    ok = AsyncMock(spec=httpx.Response)
    ok.status_code = 200
    ok.json.return_value = _gemini_payload(valid_permit_dict)

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345", "GEMINI_MODEL": "gemini-missing"}):
        with patch("httpx.AsyncClient.post", side_effect=[not_found, ok]) as mock_post:
            _, extracted_by, warnings, _ = await parse_plan_document(
                filename="plan.txt",
                content_text=sample_plan_text,
                save_upload=False,
            )

    assert mock_post.call_count == 2
    assert extracted_by == "gemini-3-flash-preview"
    assert any("Configured model gemini-missing unavailable" in w for w in warnings)


@pytest.mark.anyio
async def test_gemini_missing_fields_recovered_by_deterministic(valid_permit_dict, sample_plan_text):
    """A field Gemini left null is recovered when the plan text actually states it."""
    valid_permit_dict["structural"]["seismic_design_category"] = None
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}):
        with patch("httpx.AsyncClient.post") as mock_post:
            ok = AsyncMock(spec=httpx.Response)
            ok.status_code = 200
            ok.json.return_value = _gemini_payload(valid_permit_dict)
            mock_post.return_value = ok

            permit, extracted_by, warnings, _ = await parse_plan_document(
                filename="plan.txt",
                content_text=sample_plan_text,
                save_upload=False,
            )

    assert extracted_by == "deterministic_fallback"
    assert permit.structural.seismic_design_category is not None
    assert permit.permit_id.startswith("pm-intake-")
    assert any("structural.seismic_design_category" in w and "recovered" in w for w in warnings)


@pytest.mark.anyio
async def test_gemini_missing_fields_reports_fields_neither_parser_found(valid_permit_dict, sample_plan_text):
    valid_permit_dict["structural"]["seismic_design_category"] = None
    text = sample_plan_text.replace("Seismic Design Category:", "Seismic Zone Note:")
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}):
        with patch("httpx.AsyncClient.post") as mock_post:
            ok = AsyncMock(spec=httpx.Response)
            ok.status_code = 200
            ok.json.return_value = _gemini_payload(valid_permit_dict)
            mock_post.return_value = ok

            with pytest.raises(MissingFieldsError) as exc_info:
                await parse_plan_document(filename="plan.txt", content_text=text, save_upload=False)

    assert exc_info.value.missing_fields == ["structural.seismic_design_category"]


@pytest.mark.anyio
async def test_gemini_overall_deadline_caps_retries(sample_plan_text):
    """Slow 503s across model candidates are bounded by one overall deadline."""
    import asyncio
    import time

    async def slow_503(*args, **kwargs):
        await asyncio.sleep(0.3)
        res = AsyncMock(spec=httpx.Response)
        res.status_code = 503
        res.text = "overloaded"
        return res

    env = {"GEMINI_API_KEY": "test-key-12345", "GEMINI_MODEL": "custom-model", "GEMINI_TIMEOUT_SECONDS": "0.5"}
    with patch.dict("os.environ", env):
        with patch("httpx.AsyncClient.post", side_effect=slow_503) as mock_post:
            start = time.monotonic()
            _, extracted_by, _, _ = await parse_plan_document(
                filename="plan.txt", content_text=sample_plan_text, save_upload=False
            )
            elapsed = time.monotonic() - start

    assert extracted_by == "deterministic_fallback"
    assert mock_post.call_count == 2  # third candidate never starts
    assert elapsed < 0.9


def test_incomplete_pdf_raises_missing_fields(incomplete_pdf_bytes):
    with pytest.raises(MissingFieldsError) as exc_info:
        deterministic_plan_parse(
            content_text=None,
            filename="incomplete_plan.pdf",
            content_bytes=incomplete_pdf_bytes,
        )
    assert "applicant.name" in exc_info.value.missing_fields


def test_intake_parse_endpoint_incomplete_pdf_returns_422(app_client, incomplete_pdf_bytes):
    res = app_client.post(
        "/intake/parse",
        json={
            "filename": "incomplete_plan.pdf",
            "content_base64": base64.b64encode(incomplete_pdf_bytes).decode("ascii"),
            "mime_type": "application/pdf",
        },
    )
    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "MISSING_REQUIRED_FIELDS"
