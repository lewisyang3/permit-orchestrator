"""Verified PDF evidence: every extracted value points at real text in the upload."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.handlers.intake import load_document, parse_plan_document
from orchestrator.pdf_text import extract_pdf, locate_quote, text_document, value_in_quote

_spec = importlib.util.spec_from_file_location("make_sample_pdfs", "scripts/make_sample_pdfs.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_pdf = _mod.build_pdf

SAMPLE_TXT = Path("fixtures/sample_plans/residential_rear_addition.txt").read_text()
SAMPLE_PDF = Path("fixtures/sample_plans/residential_rear_addition.pdf").read_bytes()


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- locate_quote -------------------------------------------------------------

@pytest.fixture(scope="module")
def two_page_doc():
    return extract_pdf(build_pdf([
        "SITE PLAN\nFront Setback: 25.0 ft\nRear Setback: 32.0 ft",
        "FLOOR PLAN\nFRONT SETBACK: 18'-0\"\nStories: 2",
    ]))


def test_exact_quote_is_found_with_page_line_and_box(two_page_doc):
    loc = locate_quote(two_page_doc, "Rear Setback: 32.0 ft")
    assert (loc.page, loc.line) == (1, 3)
    [(x0, top, x1, bottom)] = loc.bboxes
    assert x0 == pytest.approx(40, abs=0.5) and x1 > x0 and bottom > top
    page = two_page_doc.pages[0]
    assert page.text[loc.char_start:loc.char_end] == "Rear Setback: 32.0 ft"


def test_case_whitespace_and_typographic_variants_match(two_page_doc):
    loc = locate_quote(two_page_doc, "front   setback:\n18′-0″")
    assert loc.page == 2
    assert two_page_doc.pages[1].text[loc.char_start:loc.char_end] == "FRONT SETBACK: 18'-0\""


def test_page_hint_picks_among_repeated_text(two_page_doc):
    # "Setback:" is on both pages: the hint decides which one is cited.
    assert locate_quote(two_page_doc, "Setback:", page_hint=2).page == 2
    assert locate_quote(two_page_doc, "Setback:", page_hint=1).page == 1
    # A wrong hint still finds text that only exists elsewhere.
    assert locate_quote(two_page_doc, "Stories: 2", page_hint=1).page == 2


@pytest.mark.parametrize("quote", ["Front Setback: 30.0 ft", "Side Setback: 5 ft", "", " ", "x"])
def test_text_not_in_document_is_not_found(two_page_doc, quote):
    assert locate_quote(two_page_doc, quote) is None


def test_quote_across_lines_gets_one_box_per_line(two_page_doc):
    loc = locate_quote(two_page_doc, "Front Setback: 25.0 ft Rear Setback")
    assert len(loc.bboxes) == 2


def test_text_document_reports_lines_without_pages():
    doc = text_document("a\nZone District: R-1\nb")
    loc = locate_quote(doc, "Zone District: R-1")
    assert (loc.page, loc.line, loc.bboxes) == (None, 2, [])


def test_truncated_number_is_not_verified():
    doc = text_document("Occupant Load: 140")
    assert locate_quote(doc, "Occupant Load: 14") is None


def test_number_inside_other_number_not_found():
    assert locate_quote(text_document("Zip: 95125"), "12") is None


@pytest.mark.parametrize(
    "text, quote",
    [
        ("Rear Setback: 20.5 ft", "Rear Setback: 20"),  # cut at a decimal point
        ("Occupant Load: 1,400", "Occupant Load: 1"),  # cut at a thousands comma
        ("Setback: 20.5 ft", "5 ft"),  # starts after a decimal point
        ("Occupant Load: 1,400", "400"),  # starts after a thousands comma
    ],
)
def test_quote_cut_inside_a_number_is_not_found(text, quote):
    assert locate_quote(text_document(text), quote) is None


@pytest.mark.parametrize(
    "text, quote",
    [
        ("Rear Setback: 20.", "Rear Setback: 20"),  # sentence-ending period
        ("Load: 14, then more", "Load: 14"),  # comma followed by a space
        ("Rear Setback: 20.5 ft", "Rear Setback: 20.5"),  # the whole number
        ("Occupant Load: 1,400", "Occupant Load: 1,400"),
    ],
)
def test_quote_ending_at_a_number_boundary_is_found(text, quote):
    assert locate_quote(text_document(text), quote) is not None


def test_later_whole_token_match_is_used():
    loc = locate_quote(text_document("Load: 140\nLoad: 14"), "Load: 14")
    assert loc.line == 2


def test_truncated_quote_flagged_by_build_evidence():
    from orchestrator.handlers.intake import build_evidence, deterministic_parse_document

    doc = text_document(Path("fixtures/sample_plans/commercial_restaurant_patio.txt").read_text())
    permit, _, _ = deterministic_parse_document(doc, "commercial_restaurant_patio.txt")
    assert permit.fire.occupant_load == 140
    permit.fire.occupant_load = 14
    evidence, warnings = build_evidence(
        permit, doc, {"fire.occupant_load": ("Occupant Load: 14", None)}, "gemini"
    )
    assert evidence["fire.occupant_load"].status == "unverified"
    assert any("not found" in w and "fire.occupant_load" in w for w in warnings)


# --- value_in_quote -----------------------------------------------------------

@pytest.mark.parametrize(
    "value,quote,expected",
    [
        (25.0, "Front Setback: 25.0 ft", True),
        (25.0, "Front Setback: 25 ft", True),
        (8400.0, "Lot Area: 8,400 sq ft", True),
        (85000.0, "Estimated Cost: $85,000 USD", True),
        (18.0, "Front Setback: 25.0 ft", False),
        ("R-1", "Zone District: R-1 (Low Density Residential)", True),
        ("R-2", "Zone District: R-1 (Low Density Residential)", False),
        ("licensed_contractor", "Role: Licensed Contractor", True),
        ("slab_on_grade", "Foundation Type: Slab-on-grade", True),
        ("CA", "State: CA", True),
        (True, "Sprinklers: yes", None),
        # feet-and-inches read as one number; its parts don't count on their own
        (18.5, "FRONT SETBACK: 18'-6\"", True),
        (18.0, "FRONT SETBACK: 18'-0\"", True),
        (18.0, "FRONT SETBACK: 18'-6\"", False),
        (6.0, "FRONT SETBACK: 18'-6\"", False),
        (18.54, "Rear Setback: 18' 6 1/2\"", True),
        (25.0, "Side Setback: 25'", True),
        (18.5, "FRONT SETBACK: 18’-6”", True),  # typographic marks
        # fractions
        (0.75, "Water Meter Size: 3/4\"", True),
        (1.5, "Meter: 1-1/2 in", True),
        (3.0, "Meter: 3/4\"", False),
        # scale suffixes
        (1200000.0, "Estimated Cost: $1.2M", True),
        (85000.0, "Estimated Cost: $85k", True),
        # state names
        ("CA", "State: California", True),
        ("NV", "State: California", False),
        # no number at all: can't tell
        (21780.0, "Lot Area: half acre", None),
    ],
)
def test_value_in_quote(value, quote, expected):
    assert value_in_quote(value, quote) is expected


def test_converted_values_verified_without_mismatch_warning():
    from orchestrator.handlers.intake import build_evidence, deterministic_plan_parse

    permit, _ = deterministic_plan_parse(SAMPLE_TXT, "plan.txt")
    permit.zoning.setback_front_ft = 18.5
    permit.property_address.state = "CA"
    text = SAMPLE_TXT + "\nFRONT SETBACK: 18'-6\"\nState: California\n"
    claims = {
        "zoning.setback_front_ft": ("FRONT SETBACK: 18'-6\"", None),
        "property_address.state": ("State: California", None),
    }
    evidence, warnings = build_evidence(permit, text_document(text), claims, "gemini")
    for path in claims:
        assert (evidence[path].status, evidence[path].value_supported) == ("verified", True), path
    assert not any("does not state" in w for w in warnings)


# --- intake: deterministic path ---------------------------------------------

@pytest.mark.anyio
async def test_sample_pdf_every_field_verified_on_page_one():
    permit, by, warnings, _ = await parse_plan_document(
        "plan.pdf", base64.b64encode(SAMPLE_PDF).decode(), save_upload=False
    )
    assert by == "deterministic_fallback"
    assert warnings == []
    assert len(permit.evidence) >= 30
    for path, ev in permit.evidence.items():
        assert ev.status == "verified", path
        assert ev.method == "deterministic"
        assert ev.page == 1 and ev.bboxes, path
        assert ev.value_supported is not False, path
    assert permit.evidence["zoning.setback_front_ft"].quote == "Front Setback: 25.0 ft"
    # The proposed total, not the existing footprint.
    assert permit.zoning.building_footprint_sqft == 2350.0
    assert permit.evidence["zoning.building_footprint_sqft"].quote == "Proposed Total Footprint: 2,350 sq ft"


@pytest.mark.anyio
async def test_text_upload_cites_lines():
    permit, _, warnings, _ = await parse_plan_document("plan.txt", content_text=SAMPLE_TXT, save_upload=False)
    ev = permit.evidence["property_address.city"]
    assert (ev.status, ev.page, ev.quote) == ("verified", None, "City: San Jose")
    assert SAMPLE_TXT.splitlines()[ev.line - 1] == "City: San Jose"
    assert warnings == []


def test_text_document_drops_page_hint_on_unverified_citation():
    # Gemini is asked for a page even on text uploads; a text document has none, so an
    # unverified citation must not point the viewer at "page 1" of a non-PDF.
    from orchestrator.handlers.intake import build_evidence, deterministic_plan_parse

    permit, _ = deterministic_plan_parse(SAMPLE_TXT, "plan.txt")
    claims = {"zoning.setback_rear_ft": ("Rear Setback: 12.0 ft", 1)}
    evidence, _ = build_evidence(permit, text_document(SAMPLE_TXT), claims, "gemini")
    rear = evidence["zoning.setback_rear_ft"]
    assert (rear.status, rear.page, rear.line) == ("unverified", None, None)


@pytest.mark.anyio
async def test_pdf_without_text_layer_is_parsed_but_unverified():
    # Text operators but no page tree: pdfplumber can't open it, the regex parser still reads
    # the raw stream, and nothing can be verified against real page text.
    escaped = (ln.replace("(", "\\(").replace(")", "\\)") for ln in SAMPLE_TXT.splitlines() if ln.strip())
    raw_pdf = b"%PDF-1.4\n" + b"\n".join(b"(" + ln.encode() + b") Tj" for ln in escaped)
    assert load_document(None, raw_pdf).kind == "raw"
    permit, _, warnings, _ = await parse_plan_document(
        "scan.pdf", base64.b64encode(raw_pdf).decode(), save_upload=False
    )
    assert permit.evidence and all(e.status == "unverified" for e in permit.evidence.values())
    assert any("no readable text layer" in w for w in warnings)


# --- intake: Gemini path ---------------------------------------------------------

def _gemini_payload(evidence: Dict[str, Any]) -> Dict[str, Any]:
    from orchestrator.handlers.intake import deterministic_plan_parse

    permit, _ = deterministic_plan_parse(SAMPLE_TXT, "plan.txt")
    body = permit.model_dump(mode="json", exclude={"permit_id", "evidence", "source_document_id"})
    body["missing_fields"] = []
    body["evidence"] = evidence
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]}


async def _parse_with_gemini(payload: Dict[str, Any], tmp_path: Optional[Path] = None):
    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch("httpx.AsyncClient.post") as post:
        resp = AsyncMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = payload
        post.return_value = resp
        return await parse_plan_document(
            "plan.pdf", base64.b64encode(SAMPLE_PDF).decode(), mime_type="application/pdf", save_upload=False
        )


@pytest.mark.anyio
async def test_gemini_citations_are_checked_against_the_pdf():
    payload = _gemini_payload({
        # real text, slightly different spacing/case
        "zoning.setback_front_ft": {"quote": "front setback:  25.0 FT", "page": 1},
        # fabricated: this text is not in the document
        "zoning.setback_rear_ft": {"quote": "Rear Setback: 12.0 ft", "page": 1},
        # real text, but it states a different value than the one extracted
        "zoning.setback_side_ft": {"quote": "Rear Setback: 32.0 ft", "page": 1},
        # malformed entries are ignored
        "zoning.lot_area_sqft": {"quote": "", "page": 1},
        "fire.occupant_load": "Occupant Load: 5",
    })
    permit, by, warnings, _ = await _parse_with_gemini(payload)
    assert "flash" in by
    ev = permit.evidence
    front = ev["zoning.setback_front_ft"]
    assert (front.status, front.method, front.quote, front.page) == ("verified", "gemini", "Front Setback: 25.0 ft", 1)
    assert front.bboxes and front.value_supported is True

    rear = ev["zoning.setback_rear_ft"]
    assert (rear.status, rear.quote, rear.bboxes) == ("unverified", "Rear Setback: 12.0 ft", [])

    side = ev["zoning.setback_side_ft"]
    assert side.status == "verified" and side.value_supported is False

    assert "zoning.lot_area_sqft" not in ev and "fire.occupant_load" not in ev
    joined = " | ".join(warnings)
    assert "Cited text was not found in the document for: zoning.setback_rear_ft." in joined
    assert "does not state the extracted value for: zoning.setback_side_ft." in joined
    assert "No source text was cited for:" in joined and "zoning.lot_area_sqft" in joined


# --- serving the document ---------------------------------------------------------

def test_uploaded_pdf_is_served_back_by_document_id(tmp_path):
    with patch("orchestrator.handlers.intake.UPLOADS_DIR", tmp_path), TestClient(create_app()) as client:
        resp = client.post("/intake/parse", json={
            "filename": "plan.pdf",
            "content_base64": base64.b64encode(SAMPLE_PDF).decode(),
            "mime_type": "application/pdf",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        doc_id = body["document_id"]
        assert doc_id and body["permit"]["source_document_id"] == doc_id
        assert body["permit"]["evidence"]["zoning.setback_front_ft"]["status"] == "verified"

        got = client.get(f"/intake/documents/{doc_id}")
        assert got.status_code == 200
        assert got.headers["content-type"] == "application/pdf"
        assert got.headers["content-disposition"] == 'inline; filename="plan.pdf"'
        assert got.content == SAMPLE_PDF


@pytest.mark.parametrize(
    "filename, content, mime_type, served_as",
    [
        # The type comes from the contents, the way load_document reads them, not the name.
        ("Plan Set", SAMPLE_PDF, "application/pdf", "application/pdf"),
        ("plan.pdf.download", SAMPLE_PDF, "application/pdf", "application/pdf"),
        ("notes", SAMPLE_TXT.encode(), "text/plain", "text/plain; charset=utf-8"),
    ],
)
def test_upload_without_known_extension_is_served_by_content(tmp_path, filename, content, mime_type, served_as):
    with patch("orchestrator.handlers.intake.UPLOADS_DIR", tmp_path), TestClient(create_app()) as client:
        resp = client.post("/intake/parse", json={
            "filename": filename,
            "content_base64": base64.b64encode(content).decode(),
            "mime_type": mime_type,
        })
        assert resp.status_code == 200, resp.text
        doc_id = resp.json()["document_id"]
        got = client.get(f"/intake/documents/{doc_id}")
        assert got.status_code == 200
        assert got.headers["content-type"] == served_as
        assert got.content == content


@pytest.mark.parametrize("bad", ["pm-intake-zzzzzzzz", "pm-intake-0000000", "..%2F..%2Fetc%2Fpasswd", "pm-intake-deadbeef"])
def test_unknown_or_malformed_document_ids_404(tmp_path, bad):
    with patch("orchestrator.handlers.intake.UPLOADS_DIR", tmp_path), TestClient(create_app()) as client:
        assert client.get(f"/intake/documents/{bad}").status_code == 404


@pytest.mark.parametrize(
    "doc_id, expected_content_type, disposition_filename",
    [
        ("sample-residential-rear-addition", "application/pdf", "residential_rear_addition.pdf"),
        ("residential_addition", "application/pdf", "residential_rear_addition.pdf"),
        ("commercial_restaurant", "text/plain; charset=utf-8", "commercial_restaurant_patio.txt"),
        ("industrial_warehouse", "text/plain; charset=utf-8", "warehouse_modernization.txt"),
    ],
)
def test_sample_documents_are_served(doc_id, expected_content_type, disposition_filename):
    with TestClient(create_app()) as client:
        resp = client.get(f"/intake/documents/{doc_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == expected_content_type
        assert disposition_filename in resp.headers["content-disposition"]
        assert len(resp.content) > 0


def test_incomplete_pdf_still_rejected():
    with TestClient(create_app()) as client:
        resp = client.post("/intake/parse", json={
            "filename": "incomplete_plan.pdf",
            "content_base64": base64.b64encode(Path("fixtures/sample_plans/incomplete_plan.pdf").read_bytes()).decode(),
        })
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "MISSING_REQUIRED_FIELDS"
