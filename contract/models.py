"""
Permit Orchestrator Shared Contract Models (Pydantic v2)
Source of truth for Orchestrator (Person A) and Department Services (Person B).
"""

from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_serializer


def format_iso_utc(dt: Union[datetime, str]) -> str:
    """Format datetime or string timestamp to RFC 3339 UTC string with Z suffix."""
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ZoneCode(str, Enum):
    R1 = "R-1"
    R2 = "R-2"
    C1 = "C-1"
    C2 = "C-2"
    MU = "MU"
    M1 = "M-1"


class OccupancyType(str, Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    ASSEMBLY = "assembly"
    INDUSTRIAL = "industrial"


class ConstructionType(str, Enum):
    WOOD_FRAME = "wood_frame"
    LIGHT_STEEL = "light_steel"
    REINFORCED_CONCRETE = "reinforced_concrete"
    MASONRY = "masonry"


class FoundationType(str, Enum):
    SLAB_ON_GRADE = "slab_on_grade"
    CRAWLSPACE = "crawlspace"
    CONTINUOUS_SPREAD_FOOTING = "continuous_spread_footing"
    DEEP_PILE = "deep_pile"


class DepartmentName(str, Enum):
    ZONING = "zoning"
    FIRE = "fire"
    WATER = "water"
    STRUCTURAL = "structural"


class DepartmentStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    PENDING = "PENDING"


class SagaState(str, Enum):
    RECEIVED = "RECEIVED"
    EVALUATING = "EVALUATING"
    DECIDED = "DECIDED"


class FinalDecision(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DecisionCode(str, Enum):
    PENDING = "PENDING"
    ALL_DEPARTMENTS_APPROVED = "ALL_DEPARTMENTS_APPROVED"
    DEPARTMENT_REJECTIONS = "DEPARTMENT_REJECTIONS"
    INCOMPLETE_REVIEWS = "INCOMPLETE_REVIEWS"


class FaultMode(str, Enum):
    NONE = "none"
    OFFLINE = "offline"
    SLOW = "slow"
    ERROR = "error"


class ApplicantRole(str, Enum):
    HOMEOWNER = "homeowner"
    LICENSED_CONTRACTOR = "licensed_contractor"
    ARCHITECT = "architect"
    ENGINEER = "engineer"


class SeismicDesignCategory(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class RuleSeverity(str, Enum):
    VIOLATION = "VIOLATION"
    ADVISORY = "ADVISORY"
    INFO = "INFO"


class SpanId(str, Enum):
    ORCHESTRATOR = "orchestrator"
    DEPT_ZONING = "dept:zoning"
    DEPT_FIRE = "dept:fire"
    DEPT_WATER = "dept:water"
    DEPT_STRUCTURAL = "dept:structural"
    CONSENSUS = "consensus"


class PropertyAddress(BaseModel):
    street: str
    city: str
    state: str
    zip: str


class Applicant(BaseModel):
    name: str
    role: ApplicantRole
    email: str
    phone: Optional[str] = None


class ZoningData(BaseModel):
    zone_code: ZoneCode
    building_height_ft: float
    lot_area_sqft: float
    building_footprint_sqft: float
    setback_front_ft: float
    setback_rear_ft: float
    setback_side_ft: float


class FireData(BaseModel):
    occupancy_type: OccupancyType
    occupant_load: int
    has_sprinkler_system: bool
    egress_exits_count: int
    distance_to_hydrant_ft: float
    has_fire_alarm: Optional[bool] = False


class WaterData(BaseModel):
    water_meter_size_inches: float
    daily_water_usage_gpd: float
    has_backflow_preventer: bool
    has_stormwater_plan: bool


class StructuralData(BaseModel):
    stories_count: int
    construction_type: ConstructionType
    seismic_design_category: SeismicDesignCategory
    foundation_type: FoundationType


class BBox(BaseModel):
    """A highlight rectangle in PDF points, origin at the page's top-left corner."""
    x0: float
    top: float
    x1: float
    bottom: float


class EvidenceSpan(BaseModel):
    """Where one extracted permit value appears in the uploaded document.

    status "verified" means the server found the text in the document itself and
    quote is the document's own text there. "unverified" means it could not (the
    parser's quote isn't in the text, or the PDF has no text layer): quote is then
    what the parser claimed, and the worker must check the value by hand.
    """
    field_path: str                     # e.g. "zoning.setback_front_ft"
    quote: str
    page: Optional[int] = None          # 1-based PDF page; None for text uploads
    line: Optional[int] = None          # 1-based line on that page
    bboxes: List[BBox] = Field(default_factory=list)
    status: Literal["verified", "unverified"]
    value_supported: Optional[bool] = None  # does the quote state the value? None: can't tell (booleans)
    method: Literal["gemini", "deterministic"]


class Permit(BaseModel):
    permit_id: Optional[str] = None
    project_title: str
    project_description: Optional[str] = None
    estimated_cost_usd: Optional[float] = 0.0
    property_address: PropertyAddress
    applicant: Applicant
    zoning: ZoningData
    fire: FireData
    water: WaterData
    structural: StructuralData
    # Uploaded plan this permit was read from, and where each value appears in it.
    source_document_id: Optional[str] = None
    evidence: Dict[str, EvidenceSpan] = Field(default_factory=dict)


class EvaluationReason(BaseModel):
    rule_id: str
    rule_name: str
    passed: bool
    severity: RuleSeverity = RuleSeverity.VIOLATION
    message: str
    # Plan side: what the applicant's document says for the fields this rule checked.
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    # Rule side: the city sources the rule cites (url, section, printed_page, ...).
    rule_sources: List[Dict[str, Any]] = Field(default_factory=list)


class DepartmentEvaluationRequest(BaseModel):
    permit_id: str
    trace_id: str
    department: DepartmentName
    attempt: int = 1
    permit: Permit


class DepartmentEvaluationResponse(BaseModel):
    permit_id: str
    department: DepartmentName
    jurisdiction_id: Optional[str] = None  # city pack the permit was evaluated against
    rule_set_id: Optional[str] = None
    status: DepartmentStatus  # APPROVED or REJECTED
    execution_time_ms: float
    reasons: List[EvaluationReason]
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_serializer("evaluated_at", when_used="json")
    def serialize_evaluated_at(self, v: datetime) -> str:
        return format_iso_utc(v)


class FaultControlRequest(BaseModel):
    department: Optional[DepartmentName] = None
    mode: FaultMode = FaultMode.NONE
    delay_ms: int = 3000
    error_code: int = 500
    error_message: str = "Simulated fault injection"


FaultControlPayload = FaultControlRequest


class FaultControlResponse(BaseModel):
    department: DepartmentName
    mode: FaultMode
    delay_ms: int = 3000
    message: str = ""


class AdminFaultResponse(BaseModel):
    zoning: FaultControlResponse
    fire: FaultControlResponse
    water: FaultControlResponse
    structural: FaultControlResponse


AdminFaultStatusResponse = AdminFaultResponse


class TraceEventType(str, Enum):
    PERMIT_RECEIVED = "PERMIT_RECEIVED"
    SPAN_STARTED = "SPAN_STARTED"
    CALL_DISPATCHED = "CALL_DISPATCHED"
    CALL_RETRYING = "CALL_RETRYING"
    CALL_SUCCEEDED = "CALL_SUCCEEDED"
    CALL_TIMED_OUT = "CALL_TIMED_OUT"
    CALL_FAILED = "CALL_FAILED"
    SPAN_COMPLETED = "SPAN_COMPLETED"
    CONSENSUS_STARTED = "CONSENSUS_STARTED"
    PERMIT_DECIDED = "PERMIT_DECIDED"


class TraceEvent(BaseModel):
    event_id: str
    trace_id: str
    permit_id: str
    span: SpanId
    parent_span: Optional[str] = None
    type: TraceEventType
    ts: Union[datetime, str] = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: Dict[str, Any] = Field(default_factory=dict)

    @field_serializer("ts", when_used="json")
    def serialize_ts(self, v: Union[datetime, str]) -> str:
        return format_iso_utc(v)


class DepartmentResultSummary(BaseModel):
    status: DepartmentStatus
    attempts: int
    latency_ms: float
    error_message: Optional[str] = None
    reasons: List[EvaluationReason] = Field(default_factory=list)


class PermitStatusResponse(BaseModel):
    permit_id: str
    trace_id: str
    state: SagaState
    final_decision: FinalDecision = FinalDecision.PENDING
    decision_code: Optional[DecisionCode] = DecisionCode.PENDING
    decision_summary: Optional[str] = None
    created_at: Union[datetime, str] = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[Union[datetime, str]] = None
    department_results: Dict[str, DepartmentResultSummary] = Field(default_factory=dict)

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, v: Union[datetime, str]) -> str:
        return format_iso_utc(v)

    @field_serializer("decided_at", when_used="json")
    def serialize_decided_at(self, v: Optional[Union[datetime, str]]) -> Optional[str]:
        if v is None:
            return None
        return format_iso_utc(v)


class SubmitPermitResponse(BaseModel):
    permit_id: str
    trace_id: str
    state: SagaState = SagaState.RECEIVED
    stream_url: str


class StreamDonePayload(BaseModel):
    permit_id: str
    final_decision: FinalDecision


StreamDoneEvent = StreamDonePayload


class IntakeParseRequest(BaseModel):
    filename: str = "plan.pdf"
    content_base64: Optional[str] = None
    content_text: Optional[str] = None
    mime_type: Optional[str] = "application/pdf"


class IntakeParseResponse(BaseModel):
    permit: Permit
    filename: str
    file_path: Optional[str] = None
    extracted_by: str = "gemini-2.5-flash"
    warnings: List[str] = Field(default_factory=list)
    # Serve the upload from GET /intake/documents/{document_id}; None if it wasn't saved.
    document_id: Optional[str] = None


class SamplePlanInfo(BaseModel):
    id: str
    title: str
    filename: str
    description: str
    mime_type: str
    sample_text: Optional[str] = None

