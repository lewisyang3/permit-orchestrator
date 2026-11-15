import { Permit, TraceEvent, PermitViolation, DepartmentName } from '../types';

export function evaluatePermitData(permit: Permit): TraceEvent[] {
  const traceId = `trc-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const permitId = permit.permit_id || `pm-${Date.now().toString(36)}`;
  const baseTime = Date.now();

  const getIso = (offsetMs: number) => new Date(baseTime + offsetMs).toISOString();

  // 1. Evaluate Zoning
  const zoningViolations: PermitViolation[] = [];
  if (permit.zoning) {
    if (permit.zoning.building_height_ft > 35) {
      zoningViolations.push({
        department: 'zoning',
        rule_id: 'SJ-Z-105',
        message: `Building height of ${permit.zoning.building_height_ft} ft exceeds maximum allowable limit of 35.0 ft for district ${permit.zoning.zone_code || ''}.`,
      });
    }
    if (permit.zoning.setback_front_ft < 15) {
      zoningViolations.push({
        department: 'zoning',
        rule_id: 'SJ-Z-101',
        message: `Front setback of ${permit.zoning.setback_front_ft} ft encroaches on minimum required setback of 15.0 ft.`,
      });
    }
    if (permit.zoning.setback_rear_ft < 15) {
      zoningViolations.push({
        department: 'zoning',
        rule_id: 'SJ-Z-104',
        message: `Rear setback of ${permit.zoning.setback_rear_ft} ft encroaches on minimum required setback of 15.0 ft.`,
      });
    }
    if (permit.zoning.setback_side_ft < 5) {
      zoningViolations.push({
        department: 'zoning',
        rule_id: 'SJ-Z-102',
        message: `Side setback of ${permit.zoning.setback_side_ft} ft encroaches on minimum required setback of 5.0 ft.`,
      });
    }
  }
  const zoningStatus = zoningViolations.length === 0 ? 'APPROVED' : 'REJECTED';

  // 2. Evaluate Fire Life Safety
  const fireViolations: PermitViolation[] = [];
  if (permit.fire) {
    const isCommercialOrAssembly =
      permit.fire.occupancy_type === 'commercial' || permit.fire.occupancy_type === 'assembly';

    if (isCommercialOrAssembly && !permit.fire.has_sprinkler_system && (permit.fire.occupant_load || 0) >= 100) {
      fireViolations.push({
        department: 'fire',
        rule_id: 'SJ-F-201',
        message: 'Automatic fire sprinkler system required for commercial occupancy with occupant load >= 100 (NFPA 13 / CBC 903.2.1.2).',
      });
    }

    if (permit.fire.egress_exits_count < 2 && (permit.fire.occupant_load || 0) > 50) {
      fireViolations.push({
        department: 'fire',
        rule_id: 'SJ-F-202',
        message: 'At least 2 separate and remote exit doors required for occupant load exceeding 50 persons (CBC 1006.2).',
      });
    }

    if (!permit.fire.has_fire_alarm) {
      fireViolations.push({
        department: 'fire',
        rule_id: 'SJ-F-203',
        message: 'Hardwired fire alarm and life safety notification system required.',
      });
    }

    if (permit.fire.distance_to_hydrant_ft > 300) {
      fireViolations.push({
        department: 'fire',
        rule_id: 'SJ-F-204',
        message: `Distance to nearest municipal fire hydrant (${permit.fire.distance_to_hydrant_ft} ft) exceeds maximum allowed 300 ft.`,
      });
    }
  }
  const fireStatus = fireViolations.length === 0 ? 'APPROVED' : 'REJECTED';

  // 3. Evaluate Water & Utilities
  const waterViolations: PermitViolation[] = [];
  if (permit.water) {
    if (permit.fire && permit.fire.occupancy_type !== 'residential' && !permit.water.has_backflow_preventer) {
      waterViolations.push({
        department: 'water',
        rule_id: 'SJ-W-301',
        message: 'Commercial/industrial service connection requires certified backflow preventer assembly (RPZ / Dual Check).',
      });
    }
    if (permit.zoning && permit.zoning.building_footprint_sqft > 5000 && !permit.water.has_stormwater_plan) {
      waterViolations.push({
        department: 'water',
        rule_id: 'SJ-W-302',
        message: 'Impervious surface footprint > 5,000 sq ft requires certified stormwater detention and mitigation plan.',
      });
    }
  }
  const waterStatus = waterViolations.length === 0 ? 'APPROVED' : 'REJECTED';

  // 4. Evaluate Structural Engineering
  const structuralViolations: PermitViolation[] = [];
  if (permit.structural) {
    if (permit.structural.stories_count > 3 && permit.structural.construction_type === 'wood_frame') {
      structuralViolations.push({
        department: 'structural',
        rule_id: 'SJ-S-401',
        message: 'Wood frame construction (Type V-B) restricted to maximum 3 stories under building code.',
      });
    }
    if (
      permit.structural.stories_count > 1 &&
      permit.structural.foundation_type === 'slab_on_grade' &&
      (permit.structural.seismic_design_category === 'D' || permit.structural.seismic_design_category === 'E')
    ) {
      structuralViolations.push({
        department: 'structural',
        rule_id: 'SJ-S-402',
        message: 'Multi-story building in high seismic zone requires engineered continuous footing or deep pile foundation.',
      });
    }
  }
  const structuralStatus = structuralViolations.length === 0 ? 'APPROVED' : 'REJECTED';

  // Consensus evaluation
  const allViolations: PermitViolation[] = [
    ...zoningViolations,
    ...fireViolations,
    ...waterViolations,
    ...structuralViolations,
  ];

  const failedDepartments: DepartmentName[] = [];
  if (zoningStatus !== 'APPROVED') failedDepartments.push('zoning');
  if (fireStatus !== 'APPROVED') failedDepartments.push('fire');
  if (waterStatus !== 'APPROVED') failedDepartments.push('water');
  if (structuralStatus !== 'APPROVED') failedDepartments.push('structural');

  const allApproved = failedDepartments.length === 0;
  const finalDecision = allApproved ? 'APPROVED' : 'REJECTED';
  const decisionCode = allApproved ? 'ALL_DEPARTMENTS_APPROVED' : 'DEPARTMENT_REJECTIONS';
  const decisionSummary = allApproved
    ? 'Consensus reached: All 4 municipal departments approved permit.'
    : `Application rejected due to department code violations: ${allViolations.map((v) => v.message).join('; ')}`;

  const departmentData: Array<{
    dept: DepartmentName;
    status: 'APPROVED' | 'REJECTED';
    violations: PermitViolation[];
    rulesEvaluated: number;
    latencyMs: number;
  }> = [
    { dept: 'zoning', status: zoningStatus, violations: zoningViolations, rulesEvaluated: 4, latencyMs: 120 },
    { dept: 'fire', status: fireStatus, violations: fireViolations, rulesEvaluated: 5, latencyMs: 140 },
    { dept: 'water', status: waterStatus, violations: waterViolations, rulesEvaluated: 3, latencyMs: 95 },
    { dept: 'structural', status: structuralStatus, violations: structuralViolations, rulesEvaluated: 4, latencyMs: 110 },
  ];

  const events: TraceEvent[] = [];

  // Root orchestrator span started
  events.push({
    event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
    trace_id: traceId,
    permit_id: permitId,
    span: 'orchestrator',
    parent_span: null,
    type: 'PERMIT_RECEIVED',
    ts: getIso(0),
    detail: {
      project_title: permit.project_title || 'Permit Review Application',
      applicant: permit.applicant?.name || 'Applicant',
      state: 'RECEIVED',
    },
  });

  // Department dispatch spans
  departmentData.forEach((d, idx) => {
    events.push({
      event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
      trace_id: traceId,
      permit_id: permitId,
      span: `dept:${d.dept}` as any,
      parent_span: 'orchestrator',
      type: 'SPAN_STARTED',
      ts: getIso(20 + idx * 5),
      detail: { department: d.dept },
    });

    events.push({
      event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
      trace_id: traceId,
      permit_id: permitId,
      span: `dept:${d.dept}` as any,
      parent_span: 'orchestrator',
      type: 'CALL_DISPATCHED',
      ts: getIso(35 + idx * 5),
      detail: { attempt: 1, endpoint: `http://${d.dept}:800${idx + 1}/evaluate` },
    });
  });

  // Department completions
  departmentData.forEach((d, idx) => {
    const compOffset = 100 + idx * 30 + d.latencyMs;

    if (d.status === 'APPROVED') {
      events.push({
        event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
        trace_id: traceId,
        permit_id: permitId,
        span: `dept:${d.dept}` as any,
        parent_span: 'orchestrator',
        type: 'CALL_SUCCEEDED',
        ts: getIso(compOffset),
        detail: {
          status: 'APPROVED',
          attempt: 1,
          latency_ms: d.latencyMs,
          rules_evaluated: d.rulesEvaluated,
        },
      });

      events.push({
        event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
        trace_id: traceId,
        permit_id: permitId,
        span: `dept:${d.dept}` as any,
        parent_span: 'orchestrator',
        type: 'SPAN_COMPLETED',
        ts: getIso(compOffset + 5),
        detail: {
          status: 'APPROVED',
          duration_ms: d.latencyMs + 15,
        },
      });
    } else {
      events.push({
        event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
        trace_id: traceId,
        permit_id: permitId,
        span: `dept:${d.dept}` as any,
        parent_span: 'orchestrator',
        type: 'CALL_FAILED',
        ts: getIso(compOffset),
        detail: {
          attempts_total: 1,
          final_status: 'REJECTED',
          error: `${d.dept.toUpperCase()} department code violations cited`,
          violations: d.violations,
        },
      });

      events.push({
        event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
        trace_id: traceId,
        permit_id: permitId,
        span: `dept:${d.dept}` as any,
        parent_span: 'orchestrator',
        type: 'SPAN_COMPLETED',
        ts: getIso(compOffset + 5),
        detail: {
          status: 'REJECTED',
          duration_ms: d.latencyMs + 15,
          violations: d.violations,
        },
      });
    }
  });

  // Consensus gate
  const consensusTime = 320;
  events.push({
    event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
    trace_id: traceId,
    permit_id: permitId,
    span: 'consensus',
    parent_span: 'orchestrator',
    type: 'CONSENSUS_STARTED',
    ts: getIso(consensusTime),
    detail: {
      departments_received: 4,
      failed_departments: failedDepartments,
    },
  });

  // Final permit decided
  events.push({
    event_id: `evt-${Math.random().toString(36).slice(2, 8)}`,
    trace_id: traceId,
    permit_id: permitId,
    span: 'orchestrator',
    parent_span: null,
    type: 'PERMIT_DECIDED',
    ts: getIso(consensusTime + 20),
    detail: {
      state: 'DECIDED',
      final_decision: finalDecision,
      decision_code: decisionCode,
      decision_summary: decisionSummary,
      violations: allViolations,
    },
  });

  return events;
}
