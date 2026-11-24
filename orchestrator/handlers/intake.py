"""Intake and plan parsing handler (Task T13).

Provides multimodal Gemini extraction (gemini-3-flash-preview) and deterministic fallback
to parse architectural plans and permit applications (PDF, text, markdown) into
strictly validated Permit JSON models per the shared contract schema.
Raises explicit validation errors when mandatory parameters are missing.
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

DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"
DEFAULT_TIMEOUT_SECONDS = 25.0
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "/data/uploads" if Path("/data").is_dir() else "uploads"))

router = APIRouter(prefix="/intake", tags=["intake"])


def _load_env_fallback() -> None:
    """Load environment variables from .env if GEMINI_API_KEY is not already set."""
    if not os.environ.get("GEMINI_API_KEY"):
        candidates = [
            Path(".env"),
            Path(__file__).resolve().parent.parent.parent / ".env",
            Path.cwd() / ".env",
        ]
        for p in candidates:
            if p.is_file():
                try:
                    for line in p.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k and k not in os.environ:
                                os.environ[k] = v
                    break
                except Exception:
                    pass


# Automatically check for .env on module load
_load_env_fallback()


# -------------------------------------------------------------------------
# Custom Exceptions
# -------------------------------------------------------------------------

class MissingFieldsError(ValueError):
    """Raised when an architectural plan is missing mandatory permit fields."""

    def __init__(self, missing_fields: List[str], message: Optional[str] = None):
        self.missing_fields = missing_fields
        msg = message or f"Plan parsing failed. Missing required fields: {', '.join(missing_fields)}"
        super().__init__(msg)


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
    street = _clean_str(addr_raw.get("street") or addr_raw.get("address"))
    city = _clean_str(addr_raw.get("city"))
    state = _clean_str(addr_raw.get("state"))
    zip_code = _clean_str(addr_raw.get("zip") or addr_raw.get("zip_code"))

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
        role_clean = app_role_raw.lower().replace(" ", "_").replace("-", "_")
        for r in ApplicantRole:
            if r.value == role_clean or r.name.lower() == role_clean:
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

    zone_str = _clean_str(zon_raw.get("zone_code") or zon_raw.get("zone_district") or zon_raw.get("zone"))
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

    b_height = _clean_float(zon_raw.get("building_height_ft") or zon_raw.get("building_height"))
    if b_height is None:
        missing_fields.append("zoning.building_height_ft")

    lot_area = _clean_float(zon_raw.get("lot_area_sqft") or zon_raw.get("lot_area"))
    if lot_area is None:
        missing_fields.append("zoning.lot_area_sqft")

    footprint = _clean_float(
        zon_raw.get("building_footprint_sqft")
        or zon_raw.get("proposed_total_footprint")
        or zon_raw.get("proposed_footprint")
        or zon_raw.get("footprint")
    )
    if footprint is None:
        missing_fields.append("zoning.building_footprint_sqft")

    sb_front = _clean_float(zon_raw.get("setback_front_ft") or zon_raw.get("front_setback"))
    if sb_front is None:
        missing_fields.append("zoning.setback_front_ft")

    sb_rear = _clean_float(zon_raw.get("setback_rear_ft") or zon_raw.get("rear_setback"))
    if sb_rear is None:
        missing_fields.append("zoning.setback_rear_ft")

    sb_side = _clean_float(zon_raw.get("setback_side_ft") or zon_raw.get("side_setback"))
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
        else (fire_raw.get("automatic_sprinklers") or fire_raw.get("sprinklers"))
    )
    if sprinklers is None:
        missing_fields.append("fire.has_sprinkler_system")

    exits = _clean_int(fire_raw.get("egress_exits_count") or fire_raw.get("exits") or fire_raw.get("egress_exits"))
    if exits is None:
        missing_fields.append("fire.egress_exits_count")

    dist_hydrant = _clean_float(
        fire_raw.get("distance_to_hydrant_ft")
        or fire_raw.get("distance_to_nearest_fire_hydrant")
        or fire_raw.get("distance_to_hydrant")
    )
    if dist_hydrant is None:
        missing_fields.append("fire.distance_to_hydrant_ft")

    # 6. Water
    water_raw = data.get("water") or data.get("water_utilities") or {}
    if not isinstance(water_raw, dict):
        water_raw = {}

    w_meter = _clean_float(
        water_raw.get("water_meter_size_inches")
        or water_raw.get("water_service_meter_size")
        or water_raw.get("water_meter_size")
    )
    if w_meter is None:
        missing_fields.append("water.water_meter_size_inches")

    daily_water = _clean_float(
        water_raw.get("daily_water_usage_gpd")
        or water_raw.get("estimated_daily_water_usage")
        or water_raw.get("daily_water_usage")
    )
    if daily_water is None:
        missing_fields.append("water.daily_water_usage_gpd")

    backflow = _clean_bool(
        water_raw.get("has_backflow_preventer")
        if "has_backflow_preventer" in water_raw
        else (water_raw.get("backflow_preventer_installed") or water_raw.get("backflow_preventer"))
    )
    if backflow is None:
        missing_fields.append("water.has_backflow_preventer")

    stormwater = _clean_bool(
        water_raw.get("has_stormwater_plan")
        if "has_stormwater_plan" in water_raw
        else (water_raw.get("stormwater_management_plan") or water_raw.get("stormwater_plan"))
    )
    if stormwater is None:
        missing_fields.append("water.has_stormwater_plan")

    # 7. Structural
    struct_raw = data.get("structural") or data.get("structural_engineering") or {}
    if not isinstance(struct_raw, dict):
        struct_raw = {}

    stories = _clean_int(struct_raw.get("stories_count") or struct_raw.get("stories"))
    if stories is None:
        missing_fields.append("structural.stories_count")

    framing_str = _clean_str(struct_raw.get("construction_type") or struct_raw.get("framing_construction"))
    construction_type: Optional[ConstructionType] = None
    if framing_str:
        f_clean = framing_str.lower()
        for c in ConstructionType:
            if c.value in f_clean or c.value.replace("_", "") in f_clean.replace(" ", "").replace("-", ""):
                construction_type = c
                break
    if not construction_type:
        missing_fields.append("structural.construction_type")

    seismic_str = _clean_str(struct_raw.get("seismic_design_category") or struct_raw.get("seismic_category"))
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
        found_clean = found_str.lower().replace(" ", "_")
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
    cost = _clean_float(data.get("estimated_cost_usd") or data.get("estimated_cost")) or 0.0
    phone = _clean_str(app_raw.get("phone"))
    alarm = _clean_bool(fire_raw.get("has_fire_alarm") or fire_raw.get("fire_alarm_system")) or False

    pid = permit_id or f"pm-intake-{uuid4().hex[:8]}"

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


def deterministic_plan_parse(
    content_text: Optional[str],
    filename: str,
    content_bytes: Optional[bytes] = None,
) -> Tuple[Permit, List[str]]:
    """Deterministically parse text or PDF architectural plan into valid Permit.

    Does not fabricate missing fields: raises MissingFieldsError if any mandatory
    field is absent or cannot be extracted.
    """
    warnings: List[str] = []
    text = content_text or ""
    if not text and content_bytes:
        text = _extract_text_from_pdf_stream(content_bytes)

    if not text.strip():
        raise MissingFieldsError(
            missing_fields=["project_title", "property_address", "applicant", "zoning", "fire", "water", "structural"],
            message=f"No readable text could be extracted from {filename}.",
        )

    # Helper regex extractors
    def _find(pattern: str) -> Optional[str]:
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    # 1. Project Title & Description
    title = _find(r"Project Title:\s*(.*)")
    description = _find(r"Project Description:\s*(.*)")
    estimated_cost = _find(r"Estimated Cost:?\s*\$?([0-9,.]+)")

    # 2. Property Address
    street = _find(r"Address:\s*(.*)")
    city = _find(r"City:\s*(.*)")
    state = _find(r"State:\s*([a-zA-Z]{2})")
    zip_code = _find(r"Zip(?: Code)?:\s*([0-9]{5})")

    # If street contains full address like "123 Main St, City, ST 12345"
    if street and (not city or not state or not zip_code):
        addr_match = re.search(r"^(.*?),\s*([A-Za-z\s]+),\s*([A-Za-z]{2})\s+([0-9]{5})$", street)
        if addr_match:
            street = addr_match.group(1).strip()
            city = city or addr_match.group(2).strip()
            state = state or addr_match.group(3).strip().upper()
            zip_code = zip_code or addr_match.group(4).strip()

    # 3. Applicant
    applicant_name = _find(r"Applicant Information:[\s\S]*?Name:\s*(.*)") or _find(r"Name:\s*(.*)")
    role_str = _find(r"Role:\s*([a-zA-Z_]+)")
    applicant_email = _find(r"Email:\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)")
    applicant_phone = _find(r"Phone:\s*([0-9() -]+)")

    # 4. Zoning
    zone_str = _find(r"Zone(?: District)?:\s*([a-zA-Z0-9-]+)")
    building_height = _find(r"Building Height:\s*([0-9.]+)")
    lot_area = _find(r"Lot Area:\s*([0-9,.]+)")
    footprint = _find(r"(?:Proposed Total Footprint|Proposed Footprint|Footprint):\s*([0-9,.]+)")
    setback_front = _find(r"Front Setback:\s*([0-9.]+)")
    setback_rear = _find(r"Rear Setback:\s*([0-9.]+)")
    setback_side = _find(r"Side Setback:\s*([0-9.]+)")

    # 5. Fire
    occ_str = _find(r"Occupancy Type:\s*([a-zA-Z]+)")
    occupant_load = _find(r"Occupant Load:\s*([0-9]+)")
    has_sprinklers = _find(r"(?:Automatic Sprinklers|Sprinkler System|Sprinklers):\s*(yes|true|no|false|present|none|not required)")
    egress_exits = _find(r"Egress Exits(?: Count)?:\s*([0-9]+)")
    dist_hydrant = _find(r"Distance to (?:Nearest )?Fire Hydrant:\s*([0-9.]+)")
    has_alarm = _find(r"Fire Alarm(?: System)?:\s*(yes|true|no|false|present|none)")

    # 6. Water
    water_meter = _find(r"Water (?:Service )?Meter Size:\s*([0-9.]+)")
    daily_water = _find(r"Estimated Daily Water Usage:\s*([0-9,.]+)")
    has_backflow = _find(r"Backflow Preventer (?:Installed)?:\s*(yes|true|no|false)")
    has_stormwater = _find(r"Stormwater (?:Management )?Plan:\s*(yes|true|no|false)")

    # 7. Structural
    stories = _find(r"Stories:\s*([0-9]+)")
    framing_str = _find(r"Framing Construction:\s*([a-zA-Z_]+)")
    seismic_str = _find(r"Seismic Design Category:\s*([A-E])")
    foundation_str = _find(r"Foundation Type:\s*([a-zA-Z_]+)")

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
    return permit, warnings


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
  "missing_fields": ["array of missing field path strings"]
}

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

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
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
            except httpx.RequestError as req_err:
                last_error = req_err
                continue

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
    _load_env_fallback()
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
            extracted_dict = _parse_gemini_json_response(raw_response)
            permit = validate_extracted_permit_dict(extracted_dict, permit_id=permit_id)
            return permit, model_used, warnings, file_path
        except MissingFieldsError:
            # Re-raise missing fields error immediately; do NOT disguise missing values
            raise
        except Exception as err:
            logger.warning("Gemini intake extraction failed (%s). Falling back to deterministic parser.", err)
            warnings.append(f"Gemini API extraction failed: {err}; fell back to deterministic parser.")

    # Fallback Path: Deterministic parser
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
    )


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
