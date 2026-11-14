import {
  TraceEvent,
  SpanId,
  DepartmentName,
  FinalDecision,
  DecisionCode,
  PermitViolation,
} from '../types';
import { getDepartmentLabel } from './formatters';

export interface SpanAttempt {
  attempt: number;
  dispatchedAt: number;
  completedAt?: number;
  durationMs?: number;
  status: 'pending' | 'success' | 'timed_out' | 'failed' | 'retrying';
  backoffMs?: number;
  error?: string;
}

export interface ParsedSpan {
  id: SpanId;
  label: string;
  parentSpan: string | null;
  department?: DepartmentName;
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'TIMEOUT' | 'ERROR';
  startTimeMs: number;
  endTimeMs: number;
  startOffsetMs: number;
  durationMs: number;
  attemptsCount: number;
  attempts: SpanAttempt[];
  events: TraceEvent[];
  violations: PermitViolation[];
  error?: string;
  rulesEvaluated?: number;
}

export interface ParsedTrace {
  traceId: string;
  permitId: string;
  projectTitle?: string;
  applicant?: string;
  startTimeMs: number;
  endTimeMs: number;
  totalDurationMs: number;
  spans: ParsedSpan[];
  finalDecision?: FinalDecision;
  decisionCode?: DecisionCode;
  decisionSummary?: string;
  violations: PermitViolation[];
  failedDepartments: DepartmentName[];
  isComplete: boolean;
}

export function parseTraceEvents(events: TraceEvent[]): ParsedTrace {
  if (!events || events.length === 0) {
    return {
      traceId: '',
      permitId: '',
      startTimeMs: 0,
      endTimeMs: 0,
      totalDurationMs: 0,
      spans: [],
      violations: [],
      failedDepartments: [],
      isComplete: false,
    };
  }

  // Sort events by timestamp
  const sorted = [...events].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());

  const traceId = sorted[0].trace_id || 'unknown-trace';
  const permitId = sorted[0].permit_id || 'unknown-permit';
  let projectTitle: string | undefined;
  let applicant: string | undefined;
  let finalDecision: FinalDecision | undefined;
  let decisionCode: DecisionCode | undefined;
  let decisionSummary: string | undefined;
  const traceViolations: PermitViolation[] = [];
  const failedDepartments: DepartmentName[] = [];
  let isComplete = false;

  const traceStartTime = new Date(sorted[0].ts).getTime();

  // Group events by span
  const spanEventMap = new Map<SpanId, TraceEvent[]>();
  for (const evt of sorted) {
    const list = spanEventMap.get(evt.span) || [];
    list.push(evt);
    spanEventMap.set(evt.span, list);

    if (evt.type === 'PERMIT_RECEIVED' && evt.detail) {
      projectTitle = evt.detail.project_title;
      applicant = evt.detail.applicant;
    }

    if (evt.type === 'PERMIT_DECIDED' && evt.detail) {
      finalDecision = evt.detail.final_decision;
      decisionCode = evt.detail.decision_code;
      decisionSummary = evt.detail.decision_summary;
      isComplete = true;
      if (Array.isArray(evt.detail.violations)) {
        for (const v of evt.detail.violations) {
          if (typeof v === 'string') {
            traceViolations.push({ department: 'fire', rule_id: 'unknown', message: v });
          } else {
            traceViolations.push(v);
          }
        }
      }
    }

    if (evt.type === 'CONSENSUS_STARTED' && evt.detail) {
      if (Array.isArray(evt.detail.failed_departments)) {
        failedDepartments.push(...evt.detail.failed_departments);
      }
    }
  }

  // Pre-determined order of spans for consistent waterfall view
  const SPAN_ORDER: SpanId[] = [
    'orchestrator',
    'dept:zoning',
    'dept:fire',
    'dept:water',
    'dept:structural',
    'consensus',
  ];

  // Also include any other spans present in the trace
  const allSpanIds = Array.from(new Set([...SPAN_ORDER, ...Array.from(spanEventMap.keys())]));

  const rawParsedSpans: Array<ParsedSpan & { rawEnd: number }> = [];

  for (const spanId of allSpanIds) {
    const spanEvts = spanEventMap.get(spanId);
    if (!spanEvts || spanEvts.length === 0) continue;

    const spanStart = new Date(spanEvts[0].ts).getTime();
    let spanEnd = new Date(spanEvts[spanEvts.length - 1].ts).getTime();

    let status: ParsedSpan['status'] = 'PENDING';
    const violations: PermitViolation[] = [];
    let rulesEvaluated: number | undefined;
    let error: string | undefined;

    const attemptsMap = new Map<number, SpanAttempt>();

    for (const evt of spanEvts) {
      const evtTime = new Date(evt.ts).getTime();

      if (evt.type === 'SPAN_COMPLETED') {
        const detailStatus = evt.detail?.status || evt.detail?.decision;
        if (detailStatus) {
          status = detailStatus.toUpperCase() as any;
        }
        if (evt.detail?.duration_ms) {
          spanEnd = Math.max(spanEnd, spanStart + evt.detail.duration_ms);
        }
      }

      if (evt.type === 'CALL_DISPATCHED') {
        const attNum = evt.detail?.attempt || 1;
        attemptsMap.set(attNum, {
          attempt: attNum,
          dispatchedAt: evtTime,
          status: 'pending',
        });
      }

      if (evt.type === 'CALL_TIMED_OUT') {
        const attNum = evt.detail?.attempt || 1;
        const current: SpanAttempt = attemptsMap.get(attNum) || {
          attempt: attNum,
          dispatchedAt: evtTime,
          status: 'pending',
        };
        current.completedAt = evtTime;
        current.durationMs = Math.max(0, evtTime - current.dispatchedAt);
        current.status = 'timed_out';
        current.error = evt.detail?.error || 'Timed out after 2.0s';
        attemptsMap.set(attNum, current);
        error = current.error;
      }

      if (evt.type === 'CALL_RETRYING') {
        const attNum = evt.detail?.attempt || 1;
        const current = attemptsMap.get(attNum);
        if (current) {
          current.status = 'retrying';
          current.backoffMs = evt.detail?.backoff_ms;
        }
      }

      if (evt.type === 'CALL_SUCCEEDED') {
        const attNum = evt.detail?.attempt || 1;
        const current: SpanAttempt = attemptsMap.get(attNum) || {
          attempt: attNum,
          dispatchedAt: evtTime,
          status: 'pending',
        };
        current.completedAt = evtTime;
        current.durationMs = evt.detail?.latency_ms || Math.max(0, evtTime - current.dispatchedAt);
        current.status = 'success';
        attemptsMap.set(attNum, current);

        if (evt.detail?.status) {
          status = evt.detail.status.toUpperCase() as any;
        }
        if (evt.detail?.rules_evaluated !== undefined) {
          rulesEvaluated = evt.detail.rules_evaluated;
        }
        if (Array.isArray(evt.detail?.violations)) {
          for (const v of evt.detail.violations) {
            if (typeof v === 'string') {
              violations.push({ department: spanId.replace('dept:', '') as DepartmentName, rule_id: 'rule', message: v });
            } else {
              violations.push(v);
            }
          }
        }
      }

      if (evt.type === 'CALL_FAILED') {
        const attNum = evt.detail?.attempts_total || 1;
        const current = attemptsMap.get(attNum);
        if (current) {
          current.completedAt = evtTime;
          current.status = 'failed';
          current.error = evt.detail?.error;
        }
        if (evt.detail?.final_status) {
          status = evt.detail.final_status.toUpperCase() as any;
        }
        error = evt.detail?.error || 'Retries exhausted';
      }

      if (evt.type === 'PERMIT_DECIDED' && spanId === 'orchestrator') {
        status = evt.detail?.final_decision?.toUpperCase() as any;
        if (evt.detail?.total_duration_ms) {
          spanEnd = Math.max(spanEnd, spanStart + evt.detail.total_duration_ms);
        }
      }
    }

    const deptName = spanId.startsWith('dept:') ? (spanId.replace('dept:', '') as DepartmentName) : undefined;
    const attemptsList = Array.from(attemptsMap.values()).sort((a, b) => a.attempt - b.attempt);

    rawParsedSpans.push({
      id: spanId,
      label: getDepartmentLabel(spanId),
      parentSpan: spanEvts[0].parent_span,
      department: deptName,
      status,
      startTimeMs: spanStart,
      endTimeMs: spanEnd,
      rawEnd: spanEnd,
      startOffsetMs: Math.max(0, spanStart - traceStartTime),
      durationMs: Math.max(1, spanEnd - spanStart),
      attemptsCount: attemptsList.length || 1,
      attempts: attemptsList,
      events: spanEvts,
      violations,
      error,
      rulesEvaluated,
    });
  }

  // Calculate trace end time from spans and events
  let traceEndTime = traceStartTime;
  for (const s of rawParsedSpans) {
    if (s.rawEnd > traceEndTime) traceEndTime = s.rawEnd;
  }
  for (const evt of sorted) {
    const t = new Date(evt.ts).getTime();
    if (t > traceEndTime) traceEndTime = t;
  }

  const totalDurationMs = Math.max(1, traceEndTime - traceStartTime);

  // If orchestrator span is present, ensure its duration covers the saga
  const orchestratorSpan = rawParsedSpans.find((s) => s.id === 'orchestrator');
  if (orchestratorSpan) {
    orchestratorSpan.endTimeMs = Math.max(orchestratorSpan.endTimeMs, traceEndTime);
    orchestratorSpan.durationMs = Math.max(1, orchestratorSpan.endTimeMs - orchestratorSpan.startTimeMs);
  }

  const spans: ParsedSpan[] = rawParsedSpans.map(({ rawEnd: _r, ...rest }) => rest);

  return {
    traceId,
    permitId,
    projectTitle,
    applicant,
    startTimeMs: traceStartTime,
    endTimeMs: traceEndTime,
    totalDurationMs,
    spans,
    finalDecision,
    decisionCode,
    decisionSummary,
    violations: traceViolations,
    failedDepartments,
    isComplete,
  };
}
