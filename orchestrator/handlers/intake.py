"""Intake and plan parsing handler (Task T13).

Provides multimodal Gemini extraction (gemini-3-flash-preview) and deterministic fallback
to parse architectural plans and permit applications (PDF, text, markdown) into
strictly validated Permit JSON models per the shared contract schema.
Raises explicit validation errors when mandatory parameters are missing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError

from contract.normalize import normalize_state
from orchestrator.pdf_text import (
    DocumentText,
    PageText,
    extract_pdf,
    line_at,
    locate_quote,
    text_document,
    value_in_quote,
)
from contract.models import (
    Applicant,
    ApplicantRole,
    BBox,
    ConstructionType,
    EvidenceSpan,
    FireData,
    FoundationType,
    IntakeParseRequest,
    IntakeParseResponse,
    OccupancyType,
    Permit,
    PropertyAddress,
    SamplePlanInfo,
    SeismicDesignCategory,
    StructuralData,
    WaterData,
    ZoneCode,
    ZoningData,
)

logger = logging.getLogger("orchestrator.intake")

DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
DEFAULT_TIMEOUT_SECONDS = 15.0
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "/data/uploads" if Path("/data").is_dir() else "uploads"))

router = APIRouter(prefix="/intake", tags=["intake"])




# -------------------------------------------------------------------------
# Custom Exceptions
# -------------------------------------------------------------------------

class MissingFieldsError(ValueError):
    """Raised when an architectural plan is missing mandatory permit fields."""

    def __init__(self, missing_fields: List[str], message: Optional[str] = None):
        self.missing_fields = missing_fields
        msg = message or f"Plan parsing failed. Missing required fields: {', '.join(missing_fields)}"
        super().__init__(msg)


class InvalidFieldsError(ValueError):
    """Raised when extracted plan fields are present but fail Permit model validation."""

    def __init__(self, errors: List[Dict[str, Any]]):
        self.errors = errors
        locs = sorted({".".join(str(p) for p in e.get("loc", ())) for e in errors})
        super().__init__(f"Plan parsing failed. Invalid field values: {', '.join(locs)}")


# -------------------------------------------------------------------------
# Built-in Sample Plans
# -------------------------------------------------------------------------

SAMPLE_PLANS: Dict[str, SamplePlanInfo] = {
    "residential_addition": SamplePlanInfo(
        id="residential_addition",
        title="Residential Rear Addition (Single Family)",
        filename="residential_rear_addition_plan.txt",
        description="Single family residential addition (R-1, 1-story wood frame, 550 sqft rear addition with compliant setbacks).",
        mime_type="text/plain",
        sample_text="""PERMIT APPLICATION & ARCHITECTURAL SPECIFICATIONS
Project Title: Single Family Residential Rear Addition
Project Description: Construction of a 550 sqft single-story rear master suite addition to an existing single-family residence.
Estimated Cost: $85,000 USD

Property Location:
Address: 742 Evergreen Terrace
City: San Jose
State: CA
Zip: 95125

Applicant Information:
Name: Homer Simpson
Role: homeowner
Email: homer.simpson@example.com
Phone: (555) 733-4663

Zoning Parameters:
Zone District: R-1 (Low Density Residential)
Building Height: 16.5 ft
Lot Area: 8,400 sq ft
Existing Footprint: 1,800 sq ft
Proposed Total Footprint: 2,350 sq ft
Front Setback: 25.0 ft
Rear Setback: 32.0 ft
Side Setback: 8.5 ft

Fire Life Safety:
Occupancy Type: residential
Occupant Load: 5 persons
Fire Alarm System: Present and hardwired
Automatic Sprinklers: not required / None
Egress Exits Count: 2 exterior exit doors
Distance to Nearest Fire Hydrant: 140 ft

Water & Utilities:
Water Service Meter Size: 0.75 inches
Estimated Daily Water Usage: 280 gpd
Backflow Preventer Installed: Yes (dual check valve)
Stormwater Management Plan: Yes (pervious pavers & rain garden)

Structural Engineering:
Stories: 1 story
Framing Construction: wood_frame (Type V-B)
Seismic Design Category: C
Foundation Type: continuous_spread_footing
""",
    ),
    "commercial_restaurant": SamplePlanInfo(
        id="commercial_restaurant",
        title="Commercial Restaurant with Outdoor Dining",
        filename="commercial_restaurant_plan.txt",
        description="Commercial restaurant renovation with patio (C-1, 140 occupant load, missing fire sprinklers, requires backflow preventer).",
        mime_type="text/plain",
        sample_text="""COMMERCIAL BUILDING PERMIT APPLICATION & PLAN
Project Title: Commercial Restaurant Outdoor Dining Expansion
Project Description: Interior dining expansion and new covered outdoor patio seating for a downtown bistro.
Estimated Cost: $210,000 USD

Property Location:
Address: 104 Main Street
City: San Jose
State: CA
Zip: 95113

Applicant Information:
Name: Gordon Ramsay
Role: licensed_contractor
Email: chef.ramsay@bistroglobal.com
Phone: (555) 867-5309

Zoning Parameters:
Zone District: C-1 (Neighborhood Commercial)
Building Height: 24.0 ft
Lot Area: 12,000 sq ft
Proposed Footprint: 5,200 sq ft
Front Setback: 10.0 ft
Rear Setback: 20.0 ft
Side Setback: 5.0 ft

Fire Life Safety:
Occupancy Type: commercial
Occupant Load: 140 persons
Fire Alarm System: Yes
Automatic Sprinklers: No (Missing suppression system)
Egress Exits Count: 1 exit door
Distance to Nearest Fire Hydrant: 95 ft

Water & Utilities:
Water Service Meter Size: 2.0 inches
Estimated Daily Water Usage: 1,850 gpd
Backflow Preventer Installed: No
Stormwater Management Plan: Yes

Structural Engineering:
Stories: 1 story
Framing Construction: light_steel
Seismic Design Category: B
Foundation Type: slab_on_grade
""",
    ),
    "industrial_warehouse": SamplePlanInfo(
        id="industrial_warehouse",
        title="Industrial Storage Warehouse Modernization",
        filename="warehouse_modernization_plan.txt",
        description="Industrial storage logistics facility (M-1, 2-story reinforced concrete, deep pile foundation).",
        mime_type="text/plain",
        sample_text="""INDUSTRIAL BUILDING PLAN SPECIFICATIONS
Project Title: Suburban Storage Warehouse Modernization
Project Description: Structural retrofit and automated racking system installation for high-bay logistics facility.
Estimated Cost: $650,000 USD

Property Location:
Address: 4800 Industrial Parkway
City: San Jose
State: CA
Zip: 95131

Applicant Information:
Name: Bruce Wayne
Role: engineer
Email: bwayne@wayneenterprises.com
Phone: (555) 234-5678

Zoning Parameters:
Zone District: M-1 (Light Industrial)
Building Height: 34.0 ft
Lot Area: 45,000 sq ft
Proposed Footprint: 18,500 sq ft
Front Setback: 40.0 ft
Rear Setback: 35.0 ft
Side Setback: 20.0 ft

Fire Life Safety:
Occupancy Type: industrial
Occupant Load: 45 persons
Fire Alarm System: Yes (Addressable multi-zone)
Automatic Sprinklers: Yes (ESFR ceiling fire suppression)
Egress Exits Count: 4 exit doors
Distance to Nearest Fire Hydrant: 110 ft

Water & Utilities:
Water Service Meter Size: 2.0 inches
Estimated Daily Water Usage: 750 gpd
Backflow Preventer Installed: Yes (RPZ assembly)
Stormwater Management Plan: Yes (Detention basin)

Structural Engineering:
Stories: 2 stories
Framing Construction: reinforced_concrete
Seismic Design Category: C
Foundation Type: deep_pile
""",
    ),
}


# -------------------------------------------------------------------------
# Parsing & Validation Helpers
# -------------------------------------------------------------------------

def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _clean_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val).strip().replace(",", "")
    m = re.search(r"[-+]?[0-9]*\.?[0-9]+", s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _clean_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    if isinstance(val, float):
        return int(val)
    s = str(val).strip().replace(",", "")
    m = re.search(r"[-+]?[0-9]+", s)
    if m:
        try:
            return int(m.group(0))
        except ValueError:
            return None
    return None


def _clean_bool(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "yes", "1", "present", "installed", "required", "y"):
        return True
    if s in ("false", "no", "0", "none", "not required", "n"):
        return False
    return None


def _normalize_token(val: str) -> str:
    """Lowercase and collapse whitespace/hyphens to underscores for enum matching."""
    return re.sub(r"[\s-]+", "_", val.strip().lower())


def _first(d: Dict[str, Any], *keys: str) -> Any:
    """Return the first value among ``keys`` that is present and not None.

    Unlike chaining ``d.get(a) or d.get(b)``, this keeps falsy-but-valid values
    such as ``0``, ``0.0`` and ``False``.
    """
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def validate_extracted_permit_dict(
    data: Dict[str, Any],
    permit_id: Optional[str] = None,
) -> Permit:
    """Validate extracted raw dictionary into a strictly validated Permit model.

    Raises MissingFieldsError if any mandatory field across Zoning, Fire, Water,
    Structural, Applicant, or Property Location is absent or unparseable.
    """
    missing_fields: List[str] = []

    # 1. Project Title
    title = _clean_str(data.get("project_title"))
    if not title:
        missing_fields.append("project_title")

    # 2. Property Address
    addr_raw = data.get("property_address") or data.get("property_location") or {}
    if not isinstance(addr_raw, dict):
        addr_raw = {}
    street = _clean_str(_first(addr_raw, "street", "address"))
    city = _clean_str(addr_raw.get("city"))
    state = normalize_state(_clean_str(addr_raw.get("state")))
    zip_code = _clean_str(_first(addr_raw, "zip", "zip_code"))

    if not street:
        missing_fields.append("property_address.street")
    if not city:
        missing_fields.append("property_address.city")
    if not state:
        missing_fields.append("property_address.state")
    if not zip_code:
        missing_fields.append("property_address.zip")

    # 3. Applicant
    app_raw = data.get("applicant") or data.get("applicant_information") or {}
    if not isinstance(app_raw, dict):
        app_raw = {}
    app_name = _clean_str(app_raw.get("name"))
    if not app_name:
        missing_fields.append("applicant.name")

    app_role_raw = _clean_str(app_raw.get("role"))
    app_role: Optional[ApplicantRole] = None
    if app_role_raw:
        role_clean = _normalize_token(app_role_raw)
        for r in ApplicantRole:
            if r.value in role_clean:
                app_role = r
                break
    if not app_role:
        missing_fields.append("applicant.role")

    app_email = _clean_str(app_raw.get("email"))
    if not app_email:
        missing_fields.append("applicant.email")

    # 4. Zoning
    zon_raw = data.get("zoning") or data.get("zoning_parameters") or {}
    if not isinstance(zon_raw, dict):
        zon_raw = {}

    zone_str = _clean_str(_first(zon_raw, "zone_code", "zone_district", "zone"))
    zone_code: Optional[ZoneCode] = None
    if zone_str:
        zm = re.search(r"\b(R-1|R-2|C-1|C-2|MU|M-1)\b", zone_str, re.IGNORECASE)
        if zm:
            try:
                zone_code = ZoneCode(zm.group(1).upper())
            except ValueError:
                pass
    if not zone_code:
        missing_fields.append("zoning.zone_code")

    b_height = _clean_float(_first(zon_raw, "building_height_ft", "building_height"))
    if b_height is None:
        missing_fields.append("zoning.building_height_ft")

    lot_area = _clean_float(_first(zon_raw, "lot_area_sqft", "lot_area"))
    if lot_area is None:
        missing_fields.append("zoning.lot_area_sqft")

    footprint = _clean_float(
        _first(zon_raw, "building_footprint_sqft", "proposed_total_footprint", "proposed_footprint", "footprint")
    )
    if footprint is None:
        missing_fields.append("zoning.building_footprint_sqft")

    sb_front = _clean_float(_first(zon_raw, "setback_front_ft", "front_setback"))
    if sb_front is None:
        missing_fields.append("zoning.setback_front_ft")

    sb_rear = _clean_float(_first(zon_raw, "setback_rear_ft", "rear_setback"))
    if sb_rear is None:
        missing_fields.append("zoning.setback_rear_ft")

    sb_side = _clean_float(_first(zon_raw, "setback_side_ft", "side_setback"))
    if sb_side is None:
        missing_fields.append("zoning.setback_side_ft")

    # 5. Fire
    fire_raw = data.get("fire") or data.get("fire_life_safety") or {}
    if not isinstance(fire_raw, dict):
        fire_raw = {}

    occ_str = _clean_str(fire_raw.get("occupancy_type"))
    occupancy_type: Optional[OccupancyType] = None
    if occ_str:
        occ_clean = occ_str.lower()
        for o in OccupancyType:
            if o.value == occ_clean:
                occupancy_type = o
                break
    if not occupancy_type:
        missing_fields.append("fire.occupancy_type")

    occ_load = _clean_int(fire_raw.get("occupant_load"))
    if occ_load is None:
        missing_fields.append("fire.occupant_load")

    sprinklers = _clean_bool(
        fire_raw.get("has_sprinkler_system")
        if "has_sprinkler_system" in fire_raw
        else (_first(fire_raw, "automatic_sprinklers", "sprinklers"))
    )
    if sprinklers is None:
        missing_fields.append("fire.has_sprinkler_system")

    exits = _clean_int(_first(fire_raw, "egress_exits_count", "exits", "egress_exits"))
    if exits is None:
        missing_fields.append("fire.egress_exits_count")

    dist_hydrant = _clean_float(
        _first(fire_raw, "distance_to_hydrant_ft", "distance_to_nearest_fire_hydrant", "distance_to_hydrant")
    )
    if dist_hydrant is None:
        missing_fields.append("fire.distance_to_hydrant_ft")

    # 6. Water
    water_raw = data.get("water") or data.get("water_utilities") or {}
    if not isinstance(water_raw, dict):
        water_raw = {}

    w_meter = _clean_float(
        _first(water_raw, "water_meter_size_inches", "water_service_meter_size", "water_meter_size")
    )
    if w_meter is None:
        missing_fields.append("water.water_meter_size_inches")

    daily_water = _clean_float(
        _first(water_raw, "daily_water_usage_gpd", "estimated_daily_water_usage", "daily_water_usage")
    )
    if daily_water is None:
        missing_fields.append("water.daily_water_usage_gpd")

    backflow = _clean_bool(
        water_raw.get("has_backflow_preventer")
        if "has_backflow_preventer" in water_raw
        else (_first(water_raw, "backflow_preventer_installed", "backflow_preventer"))
    )
    if backflow is None:
        missing_fields.append("water.has_backflow_preventer")

    stormwater = _clean_bool(
        water_raw.get("has_stormwater_plan")
        if "has_stormwater_plan" in water_raw
        else (_first(water_raw, "stormwater_management_plan", "stormwater_plan"))
    )
    if stormwater is None:
        missing_fields.append("water.has_stormwater_plan")

    # 7. Structural
    struct_raw = data.get("structural") or data.get("structural_engineering") or {}
    if not isinstance(struct_raw, dict):
        struct_raw = {}

    stories = _clean_int(_first(struct_raw, "stories_count", "stories"))
    if stories is None:
        missing_fields.append("structural.stories_count")

    framing_str = _clean_str(_first(struct_raw, "construction_type", "framing_construction"))
    construction_type: Optional[ConstructionType] = None
    if framing_str:
        f_clean = framing_str.lower()
        for c in ConstructionType:
            if c.value in f_clean or c.value.replace("_", "") in f_clean.replace(" ", "").replace("-", ""):
                construction_type = c
                break
    if not construction_type:
        missing_fields.append("structural.construction_type")

    seismic_str = _clean_str(_first(struct_raw, "seismic_design_category", "seismic_category"))
    seismic_cat: Optional[SeismicDesignCategory] = None
    if seismic_str:
        sm = re.search(r"\b([A-E])\b", seismic_str.upper())
        if sm:
            try:
                seismic_cat = SeismicDesignCategory(sm.group(1))
            except ValueError:
                pass
    if not seismic_cat:
        missing_fields.append("structural.seismic_design_category")

    found_str = _clean_str(struct_raw.get("foundation_type"))
    foundation_type: Optional[FoundationType] = None
    if found_str:
        found_clean = _normalize_token(found_str)
        for f in FoundationType:
            if f.value in found_clean or found_clean in f.value:
                foundation_type = f
                break
    if not foundation_type:
        missing_fields.append("structural.foundation_type")

    if missing_fields:
        raise MissingFieldsError(missing_fields=sorted(list(set(missing_fields))))

    # Optional fields
    desc = _clean_str(data.get("project_description"))
    cost = _clean_float(_first(data, "estimated_cost_usd", "estimated_cost")) or 0.0
    phone = _clean_str(app_raw.get("phone"))
    alarm = _clean_bool(_first(fire_raw, "has_fire_alarm", "fire_alarm_system")) or False

    pid = permit_id or f"pm-intake-{uuid4().hex[:8]}"

    try:
        return Permit(
            permit_id=pid,
            project_title=title,
            project_description=desc,
            estimated_cost_usd=cost,
            property_address=PropertyAddress(
                street=street,
                city=city,
                state=state,
                zip=zip_code,
            ),
            applicant=Applicant(
                name=app_name,
                role=app_role,
                email=app_email,
                phone=phone,
            ),
            zoning=ZoningData(
                zone_code=zone_code,
                building_height_ft=b_height,
                lot_area_sqft=lot_area,
                building_footprint_sqft=footprint,
                setback_front_ft=sb_front,
                setback_rear_ft=sb_rear,
                setback_side_ft=sb_side,
            ),
            fire=FireData(
                occupancy_type=occupancy_type,
                occupant_load=occ_load,
                has_sprinkler_system=sprinklers,
                egress_exits_count=exits,
                distance_to_hydrant_ft=dist_hydrant,
                has_fire_alarm=alarm,
            ),
            water=WaterData(
                water_meter_size_inches=w_meter,
                daily_water_usage_gpd=daily_water,
                has_backflow_preventer=backflow,
                has_stormwater_plan=stormwater,
            ),
            structural=StructuralData(
                stories_count=stories,
                construction_type=construction_type,
                seismic_design_category=seismic_cat,
                foundation_type=foundation_type,
            ),
        )
    except ValidationError as err:
        raise InvalidFieldsError(
            [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in err.errors()]
        ) from err


# -------------------------------------------------------------------------
# Deterministic Rule-Based Fallback Parser
# -------------------------------------------------------------------------

def _extract_text_from_pdf_stream(pdf_bytes: bytes) -> str:
    """Extract embedded text strings from raw PDF stream tokens without external tools."""
    try:
        raw = pdf_bytes.decode("latin1", errors="ignore")
        text_parts = []
        for match in re.finditer(r"\((.*?)\)\s*Tj", raw):
            text_parts.append(match.group(1))
        if text_parts:
            return "\n".join(text_parts)
        # Fallback to plain text blocks
        clean_text = re.sub(r"[^\x20-\x7E\n\r\t]", " ", raw)
        return clean_text
    except Exception:
        return ""


def _is_pdf(head: bytes) -> bool:
    """Whether the bytes start (after whitespace) with the PDF marker."""
    return head.lstrip()[:5] == b"%PDF-"


def load_document(
    content_text: Optional[str],
    content_bytes: Optional[bytes],
) -> DocumentText:
    """Read an upload into page-aware text.

    kind "pdf": real per-page text and word positions, so citations can be verified.
    kind "text": a plain-text upload (line numbers, no pages).
    kind "raw": a PDF without a readable text layer (scanned, or not parseable). Its
    text is a best-effort scrape for the regex parser and cannot verify citations.
    """
    if content_text:
        return text_document(content_text)
    if not content_bytes:
        return text_document("")
    if _is_pdf(content_bytes):
        try:
            doc = extract_pdf(content_bytes)
            if doc.has_text_layer:
                return doc
        except Exception as err:
            logger.warning("pdfplumber could not read the PDF (%s); using raw stream text.", err)
        return DocumentText(pages=[PageText(page=None, text=_extract_text_from_pdf_stream(content_bytes))], kind="raw")
    try:
        return text_document(content_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        return DocumentText(pages=[PageText(page=None, text=_extract_text_from_pdf_stream(content_bytes))], kind="raw")


def deterministic_plan_parse(
    content_text: Optional[str],
    filename: str,
    content_bytes: Optional[bytes] = None,
) -> Tuple[Permit, List[str]]:
    """Deterministically parse text or PDF architectural plan into valid Permit.

    Does not fabricate missing fields: raises MissingFieldsError if any mandatory
    field is absent or cannot be extracted.
    """
    permit, warnings, _ = deterministic_parse_document(load_document(content_text, content_bytes), filename)
    return permit, warnings


def deterministic_parse_document(
    doc: DocumentText,
    filename: str,
) -> Tuple[Permit, List[str], Dict[str, str]]:
    """Regex-parse a document. Also returns, per field path, the source line it read."""
    warnings: List[str] = []
    text = doc.full_text
    quotes: Dict[str, str] = {}

    if not text.strip():
        raise MissingFieldsError(
            missing_fields=["project_title", "property_address", "applicant", "zoning", "fire", "water", "structural"],
            message=f"No readable text could be extracted from {filename}.",
        )

    # Helper regex extractor: records the whole source line holding each value.
    def _find(pattern: str, path: str) -> Optional[str]:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            return None
        quotes[path] = line_at(text, m.start(1), m.end(1))
        return m.group(1).strip()

    # 1. Project Title & Description
    title = _find(r"Project Title:\s*(.*)", "project_title")
    description = _find(r"Project Description:\s*(.*)", "project_description")
    estimated_cost = _find(r"Estimated Cost:?\s*\$?([0-9,.]+)", "estimated_cost_usd")

    # 2. Property Address
    street = _find(r"Address:\s*(.*)", "property_address.street")
    city = _find(r"City:\s*(.*)", "property_address.city")
    state = _find(r"\bState:\s*(.+)", "property_address.state")
    zip_code = _find(r"Zip(?: Code)?:\s*([0-9]{5})", "property_address.zip")

    # If street contains full address like "123 Main St, City, ST 12345"
    if street and (not city or not state or not zip_code):
        addr_match = re.search(r"^(.*?),\s*([A-Za-z\s]+),\s*([A-Za-z]{2})\s+([0-9]{5})$", street)
        if addr_match:
            street_line = quotes["property_address.street"]
            for path, have in (("property_address.city", city), ("property_address.state", state),
                               ("property_address.zip", zip_code)):
                if not have:
                    quotes[path] = street_line
            street = addr_match.group(1).strip()
            city = city or addr_match.group(2).strip()
            state = state or addr_match.group(3).strip().upper()
            zip_code = zip_code or addr_match.group(4).strip()

    # 3. Applicant
    applicant_name = _find(r"Applicant Information:[\s\S]*?Name:\s*(.*)", "applicant.name") or _find(r"Name:\s*(.*)", "applicant.name")
    role_str = _find(r"Role:\s*(.+)", "applicant.role")
    applicant_email = _find(r"Email:\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", "applicant.email")
    applicant_phone = _find(r"Phone:\s*([0-9() -]+)", "applicant.phone")

    # 4. Zoning
    zone_str = _find(r"Zone(?: District)?:\s*([a-zA-Z0-9-]+)", "zoning.zone_code")
    building_height = _find(r"Building Height:\s*([0-9.]+)", "zoning.building_height_ft")
    lot_area = _find(r"Lot Area:\s*([0-9,.]+)", "zoning.lot_area_sqft")
    # Prefer the proposed total; a bare "Footprint:" label is the fallback, "Existing Footprint" never counts.
    footprint = (
        _find(r"Proposed Total Footprint:\s*([0-9,.]+)", "zoning.building_footprint_sqft")
        or _find(r"Proposed Footprint:\s*([0-9,.]+)", "zoning.building_footprint_sqft")
        or _find(r"(?<!Existing )(?<!Proposed )\bFootprint:\s*([0-9,.]+)", "zoning.building_footprint_sqft")
    )
    setback_front = _find(r"Front Setback:\s*([0-9.]+)", "zoning.setback_front_ft")
    setback_rear = _find(r"Rear Setback:\s*([0-9.]+)", "zoning.setback_rear_ft")
    setback_side = _find(r"Side Setback:\s*([0-9.]+)", "zoning.setback_side_ft")

    # 5. Fire
    occ_str = _find(r"Occupancy Type:\s*([a-zA-Z]+)", "fire.occupancy_type")
    occupant_load = _find(r"Occupant Load:\s*([0-9]+)", "fire.occupant_load")
    has_sprinklers = _find(r"(?:Automatic Sprinklers|Sprinkler System|Sprinklers):\s*(yes|true|no|false|present|none|not required)", "fire.has_sprinkler_system")
    egress_exits = _find(r"Egress Exits(?: Count)?:\s*([0-9]+)", "fire.egress_exits_count")
    dist_hydrant = _find(r"Distance to (?:Nearest )?Fire Hydrant:\s*([0-9.]+)", "fire.distance_to_hydrant_ft")
    has_alarm = _find(r"Fire Alarm(?: System)?:\s*(yes|true|no|false|present|none)", "fire.has_fire_alarm")

    # 6. Water
    water_meter = _find(r"Water (?:Service )?Meter Size:\s*([0-9.]+)", "water.water_meter_size_inches")
    daily_water = _find(r"Estimated Daily Water Usage:\s*([0-9,.]+)", "water.daily_water_usage_gpd")
    has_backflow = _find(r"Backflow Preventer (?:Installed)?:\s*(yes|true|no|false)", "water.has_backflow_preventer")
    has_stormwater = _find(r"Stormwater (?:Management )?Plan:\s*(yes|true|no|false)", "water.has_stormwater_plan")

    # 7. Structural
    stories = _find(r"Stories:\s*([0-9]+)", "structural.stories_count")
    framing_str = _find(r"Framing Construction:\s*(.+)", "structural.construction_type")
    seismic_str = _find(r"Seismic Design Category:\s*([A-E])", "structural.seismic_design_category")
    foundation_str = _find(r"Foundation Type:\s*(.+)", "structural.foundation_type")

    raw_dict = {
        "project_title": title,
        "project_description": description,
        "estimated_cost_usd": estimated_cost,
        "property_address": {
            "street": street,
            "city": city,
            "state": state,
            "zip": zip_code,
        },
        "applicant": {
            "name": applicant_name,
            "role": role_str,
            "email": applicant_email,
            "phone": applicant_phone,
        },
        "zoning": {
            "zone_code": zone_str,
            "building_height_ft": building_height,
            "lot_area_sqft": lot_area,
            "building_footprint_sqft": footprint,
            "setback_front_ft": setback_front,
            "setback_rear_ft": setback_rear,
            "setback_side_ft": setback_side,
        },
        "fire": {
            "occupancy_type": occ_str,
            "occupant_load": occupant_load,
            "has_sprinkler_system": has_sprinklers,
            "egress_exits_count": egress_exits,
            "distance_to_hydrant_ft": dist_hydrant,
            "has_fire_alarm": has_alarm,
        },
        "water": {
            "water_meter_size_inches": water_meter,
            "daily_water_usage_gpd": daily_water,
            "has_backflow_preventer": has_backflow,
            "has_stormwater_plan": has_stormwater,
        },
        "structural": {
            "stories_count": stories,
            "construction_type": framing_str,
            "seismic_design_category": seismic_str,
            "foundation_type": foundation_str,
        },
    }

    permit = validate_extracted_permit_dict(raw_dict)
    return permit, warnings, quotes


# -------------------------------------------------------------------------
# Evidence: verify where each value appears in the uploaded document
# -------------------------------------------------------------------------

_NON_EVIDENCE_FIELDS = {"permit_id", "evidence", "source_document_id"}


def _leaf_values(permit: Permit) -> Dict[str, Any]:
    """Flatten a permit to {"zoning.setback_front_ft": 25.0, ...} for non-null values."""
    out: Dict[str, Any] = {}

    def walk(prefix: str, val: Any) -> None:
        if isinstance(val, dict):
            for k, v in val.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif val is not None:
            out[prefix] = val

    walk("", permit.model_dump(mode="json", exclude=_NON_EVIDENCE_FIELDS))
    return out


def _parse_evidence_claims(raw: Any) -> Dict[str, Tuple[str, Optional[int]]]:
    """Read Gemini's {"field.path": {"quote": str, "page": int}} block, skipping malformed entries."""
    claims: Dict[str, Tuple[str, Optional[int]]] = {}
    if not isinstance(raw, dict):
        return claims
    for path, item in raw.items():
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        page = item.get("page")
        page = page if isinstance(page, int) and not isinstance(page, bool) and page > 0 else None
        claims[path] = (quote.strip(), page)
    return claims


def build_evidence(
    permit: Permit,
    doc: DocumentText,
    claims: Dict[str, Tuple[str, Optional[int]]],
    method: str,
) -> Tuple[Dict[str, EvidenceSpan], List[str]]:
    """Check each extracted value's cited text against the document itself.

    A citation is "verified" only when its text is found in the document; the stored
    quote is then the document's own text and its word boxes. Otherwise it stays
    "unverified" with the parser's claim, and a warning tells the worker to check it.
    """
    evidence: Dict[str, EvidenceSpan] = {}
    warnings: List[str] = []
    not_found: List[str] = []
    uncited: List[str] = []
    unsupported: List[str] = []
    can_verify = doc.kind != "raw" and doc.has_text_layer
    pages = {p.page: p for p in doc.pages}

    for path, value in _leaf_values(permit).items():
        quote, page_hint = claims.get(path, ("", None))
        if not quote:
            uncited.append(path)
            continue
        loc = locate_quote(doc, quote, page_hint) if can_verify else None
        if loc is None:
            if can_verify:
                not_found.append(path)
            evidence[path] = EvidenceSpan(
                # A text document has no pages: drop the parser's page hint so the
                # citation isn't shown on a page that doesn't exist.
                field_path=path, quote=quote, page=page_hint if doc.kind != "text" else None,
                status="unverified",
                value_supported=value_in_quote(value, quote), method=method,
            )
        else:
            source = pages[loc.page].text[loc.char_start:loc.char_end]
            evidence[path] = EvidenceSpan(
                field_path=path, quote=source, page=loc.page, line=loc.line,
                bboxes=[BBox(x0=b[0], top=b[1], x1=b[2], bottom=b[3]) for b in loc.bboxes],
                status="verified", value_supported=value_in_quote(value, source), method=method,
            )
        # Only text actually found in the document can be judged; unverified quotes are flagged above.
        if evidence[path].status == "verified" and evidence[path].value_supported is False:
            unsupported.append(path)

    if not can_verify and evidence:
        warnings.append(
            "The document has no readable text layer (scanned or image-only), so no citation "
            "could be checked against it. Verify every value by hand."
        )
    if not_found:
        warnings.append(
            "Cited text was not found in the document for: " + ", ".join(not_found) + ". Verify these by hand."
        )
    if uncited:
        warnings.append("No source text was cited for: " + ", ".join(uncited) + ". Verify these by hand.")
    if unsupported:
        warnings.append(
            "The cited text does not state the extracted value for: " + ", ".join(unsupported) + ". Check these."
        )
    return evidence, warnings


def _attach_evidence(
    permit: Permit,
    doc: DocumentText,
    claims: Dict[str, Tuple[str, Optional[int]]],
    method: str,
    document_id: Optional[str],
    warnings: List[str],
) -> Permit:
    evidence, ev_warnings = build_evidence(permit, doc, claims, method)
    permit.evidence = evidence
    permit.source_document_id = document_id
    warnings.extend(ev_warnings)
    return permit


# -------------------------------------------------------------------------
# Gemini Multimodal REST API Parser
# -------------------------------------------------------------------------

INTAKE_SYSTEM_PROMPT = """You are an expert municipal building permit intake officer.
Your task is to extract all architectural, zoning, fire life safety, water utility,
and structural engineering parameters from the provided building plan document.

CRITICAL INSTRUCTIONS ON MISSING INFORMATION:
- ONLY extract values that are explicitly stated, annotated, or directly verifiable in the plan document.
- NEVER invent, assume, or fabricate default or "safe compliant" values for missing information.
- If any parameter or field is not stated in the document, you MUST set its value to null.
- List all missing required field paths in the "missing_fields" array.

You MUST respond strictly with a valid JSON object matching the following structure:
{
  "project_title": "string or null",
  "project_description": "string or null",
  "estimated_cost_usd": 0.0 or null,
  "property_address": {
    "street": "string or null",
    "city": "string or null",
    "state": "string or null",
    "zip": "string or null"
  },
  "applicant": {
    "name": "string or null",
    "role": "homeowner" | "licensed_contractor" | "architect" | "engineer" | null,
    "email": "string or null",
    "phone": "string or null"
  },
  "zoning": {
    "zone_code": "R-1" | "R-2" | "C-1" | "C-2" | "MU" | "M-1" | null,
    "building_height_ft": 0.0 or null,
    "lot_area_sqft": 0.0 or null,
    "building_footprint_sqft": 0.0 or null,
    "setback_front_ft": 0.0 or null,
    "setback_rear_ft": 0.0 or null,
    "setback_side_ft": 0.0 or null
  },
  "fire": {
    "occupancy_type": "residential" | "commercial" | "assembly" | "industrial" | null,
    "occupant_load": 0 or null,
    "has_sprinkler_system": false or null,
    "egress_exits_count": 1 or null,
    "distance_to_hydrant_ft": 0.0 or null,
    "has_fire_alarm": false or null
  },
  "water": {
    "water_meter_size_inches": 0.75 or null,
    "daily_water_usage_gpd": 0.0 or null,
    "has_backflow_preventer": false or null,
    "has_stormwater_plan": false or null
  },
  "structural": {
    "stories_count": 1 or null,
    "construction_type": "wood_frame" | "light_steel" | "reinforced_concrete" | "masonry" | null,
    "seismic_design_category": "A" | "B" | "C" | "D" | "E" | null,
    "foundation_type": "slab_on_grade" | "crawlspace" | "continuous_spread_footing" | "deep_pile" | null
  },
  "missing_fields": ["array of missing field path strings"],
  "evidence": {
    "<field path, e.g. zoning.setback_front_ft>": {"quote": "exact text from the document", "page": 1}
  }
}

EVIDENCE (a reviewer uses this to check every value against the document):
- Add one "evidence" entry for every field whose value is not null, keyed by its dotted path
  (e.g. "property_address.city", "fire.occupant_load").
- "quote" must be copied character-for-character from the document: the label and value as printed,
  e.g. "FRONT SETBACK: 18'-0\"". Never paraphrase, translate, or combine text from different places.
- "page" is the 1-based page number where the quote appears (null for text documents).
- If a value comes from a drawing or image with no selectable text, still quote what is printed there.

Do not include markdown code fences (```json ... ```) in your output. Return raw JSON only.
"""


async def _call_gemini_multimodal_api(
    content_bytes: Optional[bytes],
    content_text: Optional[str],
    mime_type: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_BASE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[Dict[str, Any], str]:
    """Call Google Gemini REST API with document parts and system instructions.

    Supports automatic fallback if the configured model is unavailable.
    """
    parts: List[Dict[str, Any]] = []

    # Attach document part
    if content_bytes and mime_type in ("application/pdf", "image/png", "image/jpeg"):
        b64_data = base64.b64encode(content_bytes).decode("ascii")
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": b64_data,
            }
        })
    elif content_text:
        parts.append({
            "text": f"Document content:\n{content_text}"
        })
    elif content_bytes:
        try:
            decoded = content_bytes.decode("utf-8")
            parts.append({"text": f"Document content:\n{decoded}"})
        except Exception:
            b64_data = base64.b64encode(content_bytes).decode("ascii")
            parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": b64_data,
                }
            })

    parts.append({
        "text": INTAKE_SYSTEM_PROMPT
    })

    payload = {
        "contents": [
            {
                "parts": parts,
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    # Model resolution candidate order
    model_candidates = [model]
    if model != "gemini-3-flash-preview":
        model_candidates.append("gemini-3-flash-preview")
    if "gemini-flash-latest" not in model_candidates:
        model_candidates.append("gemini-flash-latest")

    last_error: Optional[Exception] = None

    # One overall deadline: httpx timeouts apply per phase (connect/read/...) and per
    # request, so 404/503 retries across candidates could otherwise add up.
    async with asyncio.timeout(timeout_seconds), httpx.AsyncClient(timeout=timeout_seconds) as client:
        for candidate_model in model_candidates:
            url = f"{base_url.rstrip('/')}/v1beta/models/{candidate_model}:generateContent?key={api_key}"
            try:
                res = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                if res.status_code == 200:
                    return res.json(), candidate_model
                if res.status_code in (404, 503):
                    # Model not available or temporarily overloaded; try next candidate
                    logger.warning("Gemini model %s returned status %d; attempting fallback.", candidate_model, res.status_code)
                    last_error = RuntimeError(f"Gemini API returned status {res.status_code}: {res.text}")
                    continue
                raise RuntimeError(f"Gemini API returned status {res.status_code}: {res.text}")
            except (httpx.TimeoutException, httpx.NetworkError):
                # The API itself is slow or unreachable: another model name will not
                # help, so fail fast and let the caller use the deterministic parser.
                raise

    raise last_error or RuntimeError("Gemini API call failed across all model candidates.")


def _parse_gemini_json_response(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and parse candidate JSON text from Gemini API response."""
    candidates = raw_json.get("candidates", [])
    if not candidates:
        raise ValueError("No candidates returned from Gemini API")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not parts or "text" not in parts[0]:
        raise ValueError("Candidate content missing text part")

    text = parts[0]["text"].strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    return json.loads(text.strip())


def _recover_missing_with_deterministic(
    gemini_err: MissingFieldsError,
    doc: DocumentText,
    filename: str,
    permit_id: str,
    warnings: List[str],
    file_path: Optional[str],
    document_id: Optional[str],
) -> Tuple[Permit, str, List[str], Optional[str]]:
    """Retry a Gemini extraction that missed fields with the deterministic parser.

    Returns the deterministic permit if it finds every required field. Otherwise raises
    MissingFieldsError listing the fields neither parser found (or Gemini's own list
    when the two parsers missed disjoint fields).
    """
    try:
        permit, fb_warnings, quotes = deterministic_parse_document(doc, filename)
    except MissingFieldsError as fb_err:
        fb_missing = set(fb_err.missing_fields)
        both = [f for f in gemini_err.missing_fields if f in fb_missing]
        if both:
            raise MissingFieldsError(both) from gemini_err
        raise gemini_err from fb_err
    except Exception as fb_err:
        raise gemini_err from fb_err

    logger.warning(
        "Gemini output missing fields %s; deterministic parser recovered them.",
        gemini_err.missing_fields,
    )
    permit.permit_id = permit_id
    warnings.append(
        f"Gemini output missing fields ({', '.join(gemini_err.missing_fields)}); "
        "deterministic parser recovered them."
    )
    warnings.extend(fb_warnings)
    _attach_evidence(permit, doc, {k: (v, None) for k, v in quotes.items()}, "deterministic", document_id, warnings)
    return permit, "deterministic_fallback", warnings, file_path


async def parse_plan_document(
    filename: str,
    content_base64: Optional[str] = None,
    content_text: Optional[str] = None,
    mime_type: Optional[str] = "application/pdf",
    save_upload: bool = True,
) -> Tuple[Permit, str, List[str], Optional[str]]:
    """Parse architectural plan into Permit model with Gemini and deterministic fallback.

    Raises MissingFieldsError if any required fields are missing from the input.
    """
    warnings: List[str] = []
    content_bytes: Optional[bytes] = None

    if content_base64:
        try:
            content_bytes = base64.b64decode(content_base64)
        except Exception as e:
            warnings.append(f"Failed to decode base64 content: {e}")

    # Determine mime type
    mt = (mime_type or "application/pdf").lower()
    if filename.endswith(".txt") or filename.endswith(".md"):
        mt = "text/plain"

    file_path: Optional[str] = None
    document_id: Optional[str] = None  # set once the upload is saved and can be served back
    permit_id = f"pm-intake-{uuid4().hex[:8]}"
    doc = load_document(content_text, content_bytes)

    if save_upload:
        try:
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            safe_filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
            save_name = f"{permit_id}_{safe_filename}"
            target_path = UPLOADS_DIR / save_name
            if content_bytes:
                target_path.write_bytes(content_bytes)
            elif content_text:
                target_path.write_text(content_text, encoding="utf-8")
            file_path = str(target_path)
            document_id = permit_id
        except Exception as err:
            logger.warning("Could not persist upload to disk: %s", err)
            warnings.append(f"Upload disk caching failed: {err}")

    # Primary Path: Gemini Multimodal extraction
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        base_url = os.environ.get("GEMINI_BASE_URL", os.environ.get("GEMINI_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        timeout_sec = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))

        try:
            raw_response, model_used = await _call_gemini_multimodal_api(
                content_bytes=content_bytes,
                content_text=content_text,
                mime_type=mt,
                api_key=api_key,
                model=model,
                base_url=base_url,
                timeout_seconds=timeout_sec,
            )
            if model_used != model:
                warnings.append(f"Configured model {model} unavailable; used {model_used}.")
            extracted_dict = _parse_gemini_json_response(raw_response)
            permit = validate_extracted_permit_dict(extracted_dict, permit_id=permit_id)
            claims = _parse_evidence_claims(extracted_dict.get("evidence"))
            _attach_evidence(permit, doc, claims, "gemini", document_id, warnings)
            return permit, model_used, warnings, file_path
        except InvalidFieldsError:
            # Gemini read values that fail model validation: report them, don't mask them.
            raise
        except MissingFieldsError as gemini_err:
            # Gemini left required fields empty. The deterministic parser only succeeds
            # when every required field is literally present, so trying it invents nothing.
            return _recover_missing_with_deterministic(
                gemini_err, doc, filename, permit_id, warnings, file_path, document_id
            )
        except Exception as err:
            logger.warning("Gemini intake extraction failed (%s). Falling back to deterministic parser.", err)
            warnings.append(f"Gemini API extraction failed: {err}; fell back to deterministic parser.")

    # Fallback Path: Deterministic parser
    permit, fb_warnings, quotes = deterministic_parse_document(doc, filename)
    permit.permit_id = permit_id
    warnings.extend(fb_warnings)
    _attach_evidence(permit, doc, {k: (v, None) for k, v in quotes.items()}, "deterministic", document_id, warnings)
    return permit, "deterministic_fallback", warnings, file_path


# -------------------------------------------------------------------------
# FastAPI Endpoints
# -------------------------------------------------------------------------

@router.post(
    "/parse",
    response_model=IntakeParseResponse,
    summary="Parse an uploaded architectural plan into a Permit payload",
)
async def parse_intake_plan(req: IntakeParseRequest) -> IntakeParseResponse:
    """Accept an uploaded plan document (PDF or text/markdown) and extract Permit JSON.

    Raises HTTP 422 if mandatory permit parameters are missing from the plan document.
    """
    if not req.content_base64 and not req.content_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'content_base64' or 'content_text' must be provided.",
        )

    try:
        permit, extracted_by, warnings, file_path = await parse_plan_document(
            filename=req.filename,
            content_base64=req.content_base64,
            content_text=req.content_text,
            mime_type=req.mime_type or "application/pdf",
            save_upload=True,
        )
    except MissingFieldsError as err:
        logger.warning("Plan parsing rejected due to missing fields: %s", err.missing_fields)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "MISSING_REQUIRED_FIELDS",
                "message": str(err),
                "missing_fields": err.missing_fields,
            },
        )
    except InvalidFieldsError as err:
        logger.warning("Plan parsing rejected due to invalid field values: %s", err.errors)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "INVALID_FIELD_VALUES",
                "message": str(err),
                "errors": err.errors,
            },
        )
    except Exception as err:
        logger.error("Plan parsing failed: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plan parsing failed: {err}",
        )

    return IntakeParseResponse(
        permit=permit,
        filename=req.filename,
        file_path=file_path,
        extracted_by=extracted_by,
        warnings=warnings,
        document_id=permit.source_document_id,
    )


_DOCUMENT_ID = re.compile(r"^pm-intake-[0-9a-f]{8}$")
_DOCUMENT_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".txt": "text/plain", ".md": "text/markdown"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _media_type(path: Path) -> str:
    """The served type, decided from the contents first, the same way load_document reads them.

    The extension alone isn't enough: an upload named "Plan Set" may still be a PDF that was
    parsed (and its citations verified) as one, and the viewer picks its renderer from this type.
    """
    with path.open("rb") as fh:
        head = fh.read(1024)
    if _is_pdf(head):
        return "application/pdf"
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return _DOCUMENT_TYPES[suffix]
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/markdown; charset=utf-8" if suffix == ".md" else "text/plain; charset=utf-8"


# Known sample document mappings so sample plans and fixtures can be inspected in the viewer
_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "sample_plans"
_ALT_FIXTURES_DIR = Path("fixtures/sample_plans")

SAMPLE_DOCUMENT_FILES: Dict[str, Tuple[str, str]] = {
    "sample-residential-rear-addition": ("residential_rear_addition.pdf", "application/pdf"),
    "residential_addition": ("residential_rear_addition.pdf", "application/pdf"),
    "sample-residential_addition": ("residential_rear_addition.pdf", "application/pdf"),
    "sample-commercial-restaurant": ("commercial_restaurant_patio.txt", "text/plain; charset=utf-8"),
    "commercial_restaurant": ("commercial_restaurant_patio.txt", "text/plain; charset=utf-8"),
    "sample-industrial-warehouse": ("warehouse_modernization.txt", "text/plain; charset=utf-8"),
    "industrial_warehouse": ("warehouse_modernization.txt", "text/plain; charset=utf-8"),
}


@router.get(
    "/documents/{document_id}",
    summary="Download an uploaded plan so a reviewer can check its cited evidence",
    response_class=FileResponse,
)
async def get_intake_document(document_id: str) -> Response:
    """Serve the saved upload for a permit's source_document_id."""
    # Check if this document_id is one of our built-in sample plans or fixture documents
    if document_id in SAMPLE_DOCUMENT_FILES:
        rel_name, mime = SAMPLE_DOCUMENT_FILES[document_id]
        for candidate_dir in (_FIXTURES_DIR, _ALT_FIXTURES_DIR):
            cand_path = candidate_dir / rel_name
            if cand_path.is_file():
                return FileResponse(
                    cand_path,
                    media_type=mime,
                    headers={"Content-Disposition": f'inline; filename="{rel_name}"'},
                )
        # Fallback to SAMPLE_PLANS text if file is not on disk
        plan_key = document_id.replace("sample-", "").replace("-", "_")
        if plan_key in SAMPLE_PLANS:
            info = SAMPLE_PLANS[plan_key]
            return Response(
                content=info.sample_text,
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": f'inline; filename="{info.filename}"'},
            )

    if not _DOCUMENT_ID.match(document_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    matches = sorted(UPLOADS_DIR.glob(f"{document_id}_*"))
    if not matches or not matches[0].is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    path = matches[0]
    media_type = _media_type(path)
    # inline so the browser (and pdf.js) can display it rather than download it
    return FileResponse(path, media_type=media_type,
                        headers={"Content-Disposition": f'inline; filename="{path.name[len(document_id) + 1:]}"'})


@router.get(
    "/sample-plans",
    response_model=List[SamplePlanInfo],
    summary="List available built-in sample architectural plans",
)
@router.get(
    "/sample-plans/",
    response_model=List[SamplePlanInfo],
    include_in_schema=False,
)
@router.get(
    "/samples",
    response_model=List[SamplePlanInfo],
    include_in_schema=False,
)
@router.get(
    "/samples/",
    response_model=List[SamplePlanInfo],
    include_in_schema=False,
)
async def list_sample_plans() -> List[SamplePlanInfo]:
    """Return catalog of built-in sample architectural plans for intake testing."""
    return list(SAMPLE_PLANS.values())


@router.get(
    "/sample-plans/{plan_id}",
    response_model=SamplePlanInfo,
    summary="Get details and sample text for a specific built-in plan",
)
@router.get(
    "/sample-plans/{plan_id}/",
    response_model=SamplePlanInfo,
    include_in_schema=False,
)
@router.get(
    "/samples/{plan_id}",
    response_model=SamplePlanInfo,
    include_in_schema=False,
)
@router.get(
    "/sample/{plan_id}",
    response_model=SamplePlanInfo,
    include_in_schema=False,
)
async def get_sample_plan(plan_id: str) -> SamplePlanInfo:
    """Retrieve sample plan by ID."""
    plan = SAMPLE_PLANS.get(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sample plan '{plan_id}' not found.",
        )
    return plan
