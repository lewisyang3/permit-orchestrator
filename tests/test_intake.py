"""Test suite for Task T13: Intake Plan and PDF Parsing."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from contract.models import ConstructionType, OccupancyType, Permit, ZoneCode
from orchestrator.app import create_app
from orchestrator.handlers.intake import (
    deterministic_plan_parse,
    parse_plan_document,
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
    assert permit.property_address.city == "Springfield"
    assert permit.property_address.zip == "97477"
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

            assert extracted_by == "gemini-2.5-flash"
            assert permit.project_title == "Commercial Restaurant Expansion"
            assert permit.zoning.zone_code == ZoneCode.C1
            assert permit.structural.construction_type == ConstructionType.LIGHT_STEEL
            assert len(warnings) == 0


@pytest.mark.anyio
async def test_gemini_failure_falls_back_to_deterministic():
    """Test that if Gemini call fails or times out, it gracefully falls back with warning."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-12345"}):
        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Read timeout")):
            permit, extracted_by, warnings, _ = await parse_plan_document(
                filename="warehouse_plan.txt",
                content_text="Project Title: Fallback Test Plan\nZone District: M-1\nAddress: 100 Main St\nCity: Test\nState: CA\nZip: 90001",
                save_upload=False,
            )

            assert extracted_by == "deterministic_fallback"
            assert permit.project_title == "Fallback Test Plan"
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
    assert data["extracted_by"] in ("deterministic_fallback", "gemini-2.5-flash")
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
    # List sample plans
    res = app_client.get("/intake/sample-plans")
    assert res.status_code == 200
    plans = res.json()
    assert len(plans) >= 3
    plan_ids = [p["id"] for p in plans]
    assert "residential_addition" in plan_ids
    assert "commercial_restaurant" in plan_ids

    # Get single sample plan
    res_single = app_client.get("/intake/sample-plans/residential_addition")
    assert res_single.status_code == 200
    detail = res_single.json()
    assert detail["id"] == "residential_addition"
    assert "Homer Simpson" in detail["sample_text"]

    # Non-existent sample plan
    res_404 = app_client.get("/intake/sample-plans/non_existent_plan")
    assert res_404.status_code == 404
