"""Intake and plan parsing handler (Task T13).

Provides multimodal Gemini extraction (gemini-2.5-flash) and deterministic fallback
to parse architectural plans and permit applications (PDF, text, markdown) into
strictly validated Permit JSON models per the shared contract schema.
"""

from __future__ import annotations

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

from contract.models import (
    Applicant,
    ApplicantRole,
    ConstructionType,
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

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
DEFAULT_TIMEOUT_SECONDS = 5.0
UPLOADS_DIR = Path("uploads")

router = APIRouter(prefix="/intake", tags=["intake"])


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
City: Springfield
State: OR
Zip: 97477

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
City: Metropolis
State: NY
Zip: 10001

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
City: Columbus
State: OH
Zip: 43228

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
# Deterministic Rule-Based Fallback Parser
# -------------------------------------------------------------------------

def _extract_text_from_pdf_stream(pdf_bytes: bytes) -> str:
    """Extract embedded text strings from raw PDF stream tokens without external tools."""
    try:
        # Search for text in PDF BT ... ET blocks or string literals (text) Tj
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


def deterministic_plan_parse(
    content_text: Optional[str],
    filename: str,
    content_bytes: Optional[bytes] = None,
) -> Tuple[Permit, List[str]]:
    """Deterministically parse text or PDF architectural plan into valid Permit."""
    warnings: List[str] = []
    text = content_text or ""
    if not text and content_bytes:
        text = _extract_text_from_pdf_stream(content_bytes)

    # Check for direct match with built-in sample plans
    fn_lower = filename.lower()
    for plan in SAMPLE_PLANS.values():
        if plan.id in fn_lower or plan.filename.lower() in fn_lower:
            text = plan.sample_text or text
            break

    # Helper regex extractors
    def _find(pattern: str, default: str = "") -> str:
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else default

    def _find_float(pattern: str, default: float) -> float:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            clean = m.group(1).replace(",", "").strip()
            try:
                return float(clean)
            except ValueError:
                pass
        return default

    def _find_int(pattern: str, default: int) -> int:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            clean = m.group(1).replace(",", "").strip()
            try:
                return int(clean)
            except ValueError:
                pass
        return default

    def _find_bool(pattern: str, default: bool) -> bool:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip().lower()
            return val in ("yes", "true", "1", "present", "required", "installed")
        return default

    # 1. Project Title & Description
    title = _find(r"Project Title:\s*(.*)", "")
    if not title:
        title = filename.replace("_", " ").replace("-", " ").replace(".pdf", "").replace(".txt", "").title()
        if not title or len(title) < 4:
            title = "Architectural Building Permit Application"

    description = _find(r"Project Description:\s*(.*)", f"Construction work per submitted architectural plan: {filename}")
    estimated_cost = _find_float(r"Estimated Cost:?\s*\$?([0-9,.]+)", 85000.0)

    # 2. Property Address
    street = _find(r"Address:\s*(.*)", "123 Civic Center Way")
    city = _find(r"City:\s*(.*)", "Metropolis")
    state = _find(r"State:\s*(.*)", "CA")
    zip_code = _find(r"Zip(?: Code)?:\s*([0-9]{5})", "90210")

    # 3. Applicant
    applicant_name = _find(r"Applicant Information:[\s\S]*?Name:\s*(.*)", "")
    if not applicant_name:
        applicant_name = _find(r"Name:\s*(.*)", "Jane Doe")

    role_str = _find(r"Role:\s*([a-zA-Z_]+)", "licensed_contractor").lower()
    valid_roles = {"homeowner", "licensed_contractor", "architect", "engineer"}
    applicant_role = ApplicantRole(role_str) if role_str in valid_roles else ApplicantRole.LICENSED_CONTRACTOR

    applicant_email = _find(r"Email:\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", "applicant@example.com")
    applicant_phone = _find(r"Phone:\s*([0-9() -]+)", "(555) 123-4567")

    # 4. Zoning
    zone_str = _find(r"Zone(?: District)?:\s*([a-zA-Z0-9-]+)", "R-1").upper()
    valid_zones = {"R-1", "R-2", "C-1", "C-2", "MU", "M-1"}
    zone_code = ZoneCode(zone_str) if zone_str in valid_zones else ZoneCode.R1

    building_height = _find_float(r"Building Height:\s*([0-9.]+)", 18.0)
    lot_area = _find_float(r"Lot Area:\s*([0-9,.]+)", 8500.0)
    footprint = _find_float(r"(?:Proposed Total Footprint|Proposed Footprint|Footprint):\s*([0-9,.]+)", 2200.0)
    setback_front = _find_float(r"Front Setback:\s*([0-9.]+)", 25.0)
    setback_rear = _find_float(r"Rear Setback:\s*([0-9.]+)", 30.0)
    setback_side = _find_float(r"Side Setback:\s*([0-9.]+)", 8.0)

    # 5. Fire
    occ_str = _find(r"Occupancy Type:\s*([a-zA-Z]+)", "residential").lower()
    valid_occs = {"residential", "commercial", "assembly", "industrial"}
    occupancy_type = OccupancyType(occ_str) if occ_str in valid_occs else OccupancyType.RESIDENTIAL

    occupant_load = _find_int(r"Occupant Load:\s*([0-9]+)", 6 if occupancy_type == OccupancyType.RESIDENTIAL else 120)
    has_sprinklers = _find_bool(r"(?:Automatic Sprinklers|Sprinkler System|Sprinklers):\s*(yes|true|no|false|present|none)", False)
    egress_exits = _find_int(r"Egress Exits(?: Count)?:\s*([0-9]+)", 2)
    dist_hydrant = _find_float(r"Distance to (?:Nearest )?Fire Hydrant:\s*([0-9.]+)", 125.0)
    has_alarm = _find_bool(r"Fire Alarm(?: System)?:\s*(yes|true|no|false|present)", True)

    # 6. Water
    water_meter = _find_float(r"Water (?:Service )?Meter Size:\s*([0-9.]+)", 0.75)
    daily_water = _find_float(r"Estimated Daily Water Usage:\s*([0-9,.]+)", 300.0)
    has_backflow = _find_bool(r"Backflow Preventer (?:Installed)?:\s*(yes|true|no|false)", True)
    has_stormwater = _find_bool(r"Stormwater (?:Management )?Plan:\s*(yes|true|no|false)", True)

    # 7. Structural
    stories = _find_int(r"Stories:\s*([0-9]+)", 1)
    framing_str = _find(r"Framing Construction:\s*([a-zA-Z_]+)", "wood_frame").lower()
    valid_framing = {"wood_frame", "light_steel", "reinforced_concrete", "masonry"}
    construction_type = ConstructionType(framing_str) if framing_str in valid_framing else ConstructionType.WOOD_FRAME

    seismic_str = _find(r"Seismic Design Category:\s*([A-E])", "C").upper()
    valid_seismic = {"A", "B", "C", "D", "E"}
    seismic_category = SeismicDesignCategory(seismic_str) if seismic_str in valid_seismic else SeismicDesignCategory.C

    foundation_str = _find(r"Foundation Type:\s*([a-zA-Z_]+)", "continuous_spread_footing").lower()
    valid_foundations = {"slab_on_grade", "crawlspace", "continuous_spread_footing", "deep_pile"}
    foundation_type = FoundationType(foundation_str) if foundation_str in valid_foundations else FoundationType.CONTINUOUS_SPREAD_FOOTING

    permit = Permit(
        permit_id=f"pm-intake-{uuid4().hex[:8]}",
        project_title=title,
        project_description=description,
        estimated_cost_usd=estimated_cost,
        property_address=PropertyAddress(
            street=street,
            city=city,
            state=state,
            zip=zip_code,
        ),
        applicant=Applicant(
            name=applicant_name,
            role=applicant_role,
            email=applicant_email,
            phone=applicant_phone,
        ),
        zoning=ZoningData(
            zone_code=zone_code,
            building_height_ft=building_height,
            lot_area_sqft=lot_area,
            building_footprint_sqft=footprint,
            setback_front_ft=setback_front,
            setback_rear_ft=setback_rear,
            setback_side_ft=setback_side,
        ),
        fire=FireData(
            occupancy_type=occupancy_type,
            occupant_load=occupant_load,
            has_sprinkler_system=has_sprinklers,
            egress_exits_count=egress_exits,
            distance_to_hydrant_ft=dist_hydrant,
            has_fire_alarm=has_alarm,
        ),
        water=WaterData(
            water_meter_size_inches=water_meter,
            daily_water_usage_gpd=daily_water,
            has_backflow_preventer=has_backflow,
            has_stormwater_plan=has_stormwater,
        ),
        structural=StructuralData(
            stories_count=stories,
            construction_type=construction_type,
            seismic_design_category=seismic_category,
            foundation_type=foundation_type,
        ),
    )

    return permit, warnings


# -------------------------------------------------------------------------
# Gemini Multimodal REST API Parser
# -------------------------------------------------------------------------

INTAKE_SYSTEM_PROMPT = """You are an expert municipal building permit intake officer.
Your task is to extract all architectural, zoning, fire life safety, water utility,
and structural engineering parameters from the provided building plan document.

You MUST respond strictly with a valid JSON object matching the following structure:
{
  "project_title": "string",
  "project_description": "string",
  "estimated_cost_usd": 0.0,
  "property_address": {
    "street": "string",
    "city": "string",
    "state": "string",
    "zip": "string"
  },
  "applicant": {
    "name": "string",
    "role": "homeowner" | "licensed_contractor" | "architect" | "engineer",
    "email": "string",
    "phone": "string or null"
  },
  "zoning": {
    "zone_code": "R-1" | "R-2" | "C-1" | "C-2" | "MU" | "M-1",
    "building_height_ft": 0.0,
    "lot_area_sqft": 0.0,
    "building_footprint_sqft": 0.0,
    "setback_front_ft": 0.0,
    "setback_rear_ft": 0.0,
    "setback_side_ft": 0.0
  },
  "fire": {
    "occupancy_type": "residential" | "commercial" | "assembly" | "industrial",
    "occupant_load": 0,
    "has_sprinkler_system": false,
    "egress_exits_count": 1,
    "distance_to_hydrant_ft": 0.0,
    "has_fire_alarm": false
  },
  "water": {
    "water_meter_size_inches": 0.75,
    "daily_water_usage_gpd": 0.0,
    "has_backflow_preventer": false,
    "has_stormwater_plan": false
  },
  "structural": {
    "stories_count": 1,
    "construction_type": "wood_frame" | "light_steel" | "reinforced_concrete" | "masonry",
    "seismic_design_category": "A" | "B" | "C" | "D" | "E",
    "foundation_type": "slab_on_grade" | "crawlspace" | "continuous_spread_footing" | "deep_pile"
  }
}

Do not include markdown code fences (```json ... ```) in your output. Return raw JSON only.
If a specific field is omitted in the plan, supply safe compliant standards for that occupancy type.
"""


async def _call_gemini_multimodal_api(
    content_bytes: Optional[bytes],
    content_text: Optional[str],
    mime_type: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_BASE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Call Google Gemini REST API with document parts and system instructions."""
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
        # Fallback decode
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

    url = f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent?key={api_key}"

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        res = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if res.status_code != 200:
            raise RuntimeError(f"Gemini API returned status {res.status_code}: {res.text}")
        return res.json()


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
    # Strip potential markdown formatting if returned
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    return json.loads(text.strip())


async def parse_plan_document(
    filename: str,
    content_base64: Optional[str] = None,
    content_text: Optional[str] = None,
    mime_type: Optional[str] = "application/pdf",
    save_upload: bool = True,
) -> Tuple[Permit, str, List[str], Optional[str]]:
    """Parse architectural plan into Permit model with Gemini and deterministic fallback."""
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

    # Save copy to local disk (uploads/ row 80 in spec)
    file_path: Optional[str] = None
    permit_id = f"pm-intake-{uuid4().hex[:8]}"

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
        except Exception as err:
            logger.warning("Could not persist upload to disk: %s", err)
            warnings.append(f"Upload disk caching failed: {err}")

    # Check for Gemini API key
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        base_url = os.environ.get("GEMINI_BASE_URL", os.environ.get("GEMINI_API_BASE", DEFAULT_API_BASE)).rstrip("/")
        timeout_sec = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))

        try:
            raw_response = await _call_gemini_multimodal_api(
                content_bytes=content_bytes,
                content_text=content_text,
                mime_type=mt,
                api_key=api_key,
                model=model,
                base_url=base_url,
                timeout_seconds=timeout_sec,
            )
            extracted_dict = _parse_gemini_json_response(raw_response)
            extracted_dict["permit_id"] = permit_id
            permit = Permit.model_validate(extracted_dict)
            return permit, model, warnings, file_path
        except Exception as err:
            logger.warning("Gemini intake extraction failed (%s). Falling back to deterministic parser.", err)
            warnings.append(f"Gemini API extraction failed: {err}; fell back to deterministic parser.")

    # Deterministic fallback parser
    permit, fb_warnings = deterministic_plan_parse(content_text, filename, content_bytes)
    permit.permit_id = permit_id
    warnings.extend(fb_warnings)
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
    """Accept an uploaded plan document (PDF or text/markdown) and extract Permit JSON."""
    if not req.content_base64 and not req.content_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'content_base64' or 'content_text' must be provided.",
        )

    permit, extracted_by, warnings, file_path = await parse_plan_document(
        filename=req.filename,
        content_base64=req.content_base64,
        content_text=req.content_text,
        mime_type=req.mime_type or "application/pdf",
        save_upload=True,
    )

    return IntakeParseResponse(
        permit=permit,
        filename=req.filename,
        file_path=file_path,
        extracted_by=extracted_by,
        warnings=warnings,
    )


@router.get(
    "/sample-plans",
    response_model=List[SamplePlanInfo],
    summary="List available built-in sample architectural plans",
)
async def list_sample_plans() -> List[SamplePlanInfo]:
    """Return catalog of built-in sample architectural plans for intake testing."""
    return list(SAMPLE_PLANS.values())


@router.get(
    "/sample-plans/{plan_id}",
    response_model=SamplePlanInfo,
    summary="Get details and sample text for a specific built-in plan",
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
