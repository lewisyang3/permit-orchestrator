/**
 * Permit Orchestrator Shared TypeScript Contract
 * Used by Person B for Trace UI (React + Tailwind) and client/service interactions.
 */

export type DepartmentName = 'zoning' | 'fire' | 'water' | 'structural';

export type DepartmentStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'TIMEOUT' | 'ERROR';

export type SagaState = 'RECEIVED' | 'EVALUATING' | 'DECIDED';

export type FinalDecision = 'PENDING' | 'APPROVED' | 'REJECTED';

export type DecisionCode =
  | 'PENDING'
  | 'ALL_DEPARTMENTS_APPROVED'
  | 'DEPARTMENT_REJECTIONS'
  | 'INCOMPLETE_REVIEWS';

export type FaultMode = 'none' | 'offline' | 'slow' | 'error';

export type ApplicantRole = 'homeowner' | 'licensed_contractor' | 'architect' | 'engineer';

export type ZoneCode = 'R-1' | 'R-2' | 'C-1' | 'C-2' | 'MU' | 'M-1';

export type OccupancyType = 'residential' | 'commercial' | 'assembly' | 'industrial';

export type ConstructionType = 'wood_frame' | 'light_steel' | 'reinforced_concrete' | 'masonry';

export type FoundationType = 'slab_on_grade' | 'crawlspace' | 'continuous_spread_footing' | 'deep_pile';

export type SeismicDesignCategory = 'A' | 'B' | 'C' | 'D' | 'E';

export type RuleSeverity = 'VIOLATION' | 'ADVISORY' | 'INFO';

export interface PropertyAddress {
  street: string;
  city: string;
  state: string;
  zip: string;
}

export interface Applicant {
  name: string;
  role: ApplicantRole;
  email: string;
  phone?: string | null;
}

export interface ZoningData {
  zone_code: ZoneCode;
  building_height_ft: number;
  lot_area_sqft: number;
  building_footprint_sqft: number;
  setback_front_ft: number;
  setback_rear_ft: number;
  setback_side_ft: number;
}

export interface FireData {
  occupancy_type: OccupancyType;
  occupant_load: number;
  has_sprinkler_system: boolean;
  egress_exits_count: number;
  distance_to_hydrant_ft: number;
  has_fire_alarm?: boolean;
}

export interface WaterData {
  water_meter_size_inches: number;
  daily_water_usage_gpd: number;
  has_backflow_preventer: boolean;
  has_stormwater_plan: boolean;
}

export interface StructuralData {
  stories_count: number;
  construction_type: ConstructionType;
  seismic_design_category: SeismicDesignCategory;
  foundation_type: FoundationType;
}

export interface Permit {
  permit_id?: string; // Optional on intake submission; assigned by orchestrator if omitted
  project_title: string;
  project_description?: string | null;
  estimated_cost_usd?: number;
  property_address: PropertyAddress;
  applicant: Applicant;
  zoning: ZoningData;
  fire: FireData;
  water: WaterData;
  structural: StructuralData;
  /** Uploaded plan this permit was read from (GET /intake/documents/{id}). */
  source_document_id?: string | null;
  /** Where each value appears in that document, keyed by field path. */
  evidence?: Record<string, EvidenceSpan>;
}

/** Highlight rectangle in PDF points, origin at the page's top-left corner. */
export interface BBox {
  x0: number;
  top: number;
  x1: number;
  bottom: number;
}

/**
 * Where one extracted value appears in the uploaded document.
 * "verified": the server found this text in the document; quote is the document's own text.
 * "unverified": it could not; quote is the parser's claim and must be checked by hand.
 */
export interface EvidenceSpan {
  field_path: string; // e.g. "zoning.setback_front_ft"
  quote: string;
  page?: number | null; // 1-based; null for text uploads
  line?: number | null;
  bboxes: BBox[];
  status: 'verified' | 'unverified';
  value_supported?: boolean | null; // does the quote state the value? null: can't tell
  method: 'gemini' | 'deterministic';
}

/** A city source a rule cites (from the rule pack's source entries). */
export interface RuleSource {
  url?: string;
  section?: string;
  printed_page?: number;
  source_type?: string;
  retrieval?: string;
  checked_on?: string;
  [key: string]: unknown;
}

export interface EvaluationReason {
  rule_id: string;
  rule_name: string;
  passed: boolean;
  severity?: RuleSeverity;
  message: string;
  /** What the applicant's document says for the fields this rule checked. */
  evidence?: EvidenceSpan[];
  /** City sources the rule cites. */
  rule_sources?: RuleSource[];
}

export interface PermitViolation {
  department: DepartmentName;
  rule_id: string;
  message: string;
  rule_name?: string;
  /** What the applicant's document says for the fields this rule checked. */
  evidence?: EvidenceSpan[];
  /** City sources the rule cites. */
  rule_sources?: RuleSource[];
}

/** One department reason as carried on trace events (detail.findings). */
export interface DepartmentFinding extends EvaluationReason {
  department: DepartmentName;
}

export type TraceEventType =
  | 'PERMIT_RECEIVED'
  | 'SPAN_STARTED'
  | 'CALL_DISPATCHED'
  | 'CALL_RETRYING'
  | 'CALL_SUCCEEDED'
  | 'CALL_TIMED_OUT'
  | 'CALL_FAILED'
  | 'SPAN_COMPLETED'
  | 'CONSENSUS_STARTED'
  | 'PERMIT_DECIDED';

export type SpanId =
  | 'orchestrator'
  | 'dept:zoning'
  | 'dept:fire'
  | 'dept:water'
  | 'dept:structural'
  | 'consensus';

// Strongly typed event details for UI and waterfall rendering
export interface PermitReceivedDetail {
  project_title: string;
  applicant: string;
  state: SagaState;
  [key: string]: any;
}

export interface SpanStartedDetail {
  department: DepartmentName;
  [key: string]: any;
}

export interface CallDispatchedDetail {
  attempt: number;
  endpoint: string;
  [key: string]: any;
}

export interface CallRetryingDetail {
  attempt: number;
  next_attempt: number;
  backoff_ms: number;
  [key: string]: any;
}

export interface CallTimedOutDetail {
  attempt: number;
  timeout_seconds: number;
  error: string;
  [key: string]: any;
}

export interface CallFailedDetail {
  attempts_total: number;
  final_status: DepartmentStatus;
  error: string;
  [key: string]: any;
}

export interface CallSucceededDetail {
  status: DepartmentStatus;
  attempt: number;
  latency_ms: number;
  rules_evaluated?: number;
  violations?: PermitViolation[] | string[];
  [key: string]: any;
}

export interface SpanCompletedDetail {
  status?: DepartmentStatus;
  decision?: FinalDecision;
  duration_ms: number;
  attempts?: number;
  [key: string]: any;
}

export interface ConsensusStartedDetail {
  departments_responded: number;
  approvals?: number;
  rejections?: number;
  failures?: number;
  departments_failed?: number;
  failed_departments?: DepartmentName[];
  [key: string]: any;
}

export interface PermitDecidedDetail {
  state: SagaState;
  final_decision: FinalDecision;
  decision_code: DecisionCode;
  decision_summary: string;
  total_duration_ms: number;
  violations?: PermitViolation[];
  [key: string]: any;
}

export type TraceEventDetail =
  | PermitReceivedDetail
  | SpanStartedDetail
  | CallDispatchedDetail
  | CallRetryingDetail
  | CallTimedOutDetail
  | CallFailedDetail
  | CallSucceededDetail
  | SpanCompletedDetail
  | ConsensusStartedDetail
  | PermitDecidedDetail
  | Record<string, any>;

export interface TraceEvent {
  event_id: string;
  trace_id: string;
  permit_id: string;
  span: SpanId;
  parent_span: string | null;
  type: TraceEventType;
  ts: string;
  detail: TraceEventDetail;
}

export interface DepartmentResultSummary {
  status: DepartmentStatus;
  attempts: number;
  latency_ms: number;
  error_message?: string | null;
  reasons: EvaluationReason[];
}

export interface PermitStatusResponse {
  permit_id: string;
  trace_id: string;
  state: SagaState;
  final_decision: FinalDecision;
  decision_code?: DecisionCode | null;
  decision_summary?: string | null;
  created_at: string;
  decided_at?: string | null;
  department_results: Record<DepartmentName, DepartmentResultSummary>;
}

export interface FaultControlPayload {
  department?: DepartmentName | null;
  mode: FaultMode;
  delay_ms?: number;
  error_code?: number;
  error_message?: string;
}

export type FaultControlRequest = FaultControlPayload;

export interface FaultControlResponse {
  department: DepartmentName;
  mode: FaultMode;
  delay_ms: number;
  message: string;
}

export type AdminFaultStatusResponse = Record<DepartmentName, FaultControlResponse>;
export type AdminFaultResponse = AdminFaultStatusResponse;

export interface SubmitPermitResponse {
  permit_id: string;
  trace_id: string;
  state: SagaState;
  stream_url: string;
}

export interface SSEDonePayload {
  permit_id: string;
  final_decision: FinalDecision;
}

export type StreamDonePayload = SSEDonePayload;
export type StreamDoneEvent = SSEDonePayload;

export interface DepartmentEvaluationRequest {
  permit_id: string;
  trace_id: string;
  department: DepartmentName;
  attempt: number;
  permit: Permit;
}

export interface DepartmentEvaluationResponse {
  permit_id: string;
  department: DepartmentName;
  /** City rule pack the permit was evaluated against, e.g. "ca-san-jose". */
  jurisdiction_id?: string | null;
  rule_set_id?: string | null;
  status: 'APPROVED' | 'REJECTED';
  execution_time_ms: number;
  reasons: EvaluationReason[];
  evaluated_at: string;
}

export interface IntakeParseRequest {
  filename: string;
  content_base64?: string | null;
  content_text?: string | null;
  mime_type?: string | null;
}

export interface IntakeParseResponse {
  permit: Permit;
  filename: string;
  file_path?: string | null;
  extracted_by: string;
  warnings: string[];
  /** Fetch the upload from GET /intake/documents/{document_id}. */
  document_id?: string | null;
}

export interface SamplePlanInfo {
  id: string;
  title: string;
  filename: string;
  description: string;
  mime_type: string;
  sample_text?: string | null;
}
