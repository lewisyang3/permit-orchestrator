import permitApprovedJson from './permit_approved.json';
import permitRejectedFireJson from './permit_rejected_fire.json';
import permitTimeoutOfflineJson from './permit_timeout_offline.json';

import traceEventsApprovedJson from './trace_events_approved.json';
import traceEventsRejectedJson from './trace_events_rejected.json';
import traceEventsTimeoutJson from './trace_events_timeout.json';

import { Permit, TraceEvent } from '../types';

export interface FixtureScenario {
  id: 'approved' | 'rejected' | 'timeout';
  title: string;
  subtitle: string;
  description: string;
  expectedDecision: 'APPROVED' | 'REJECTED';
  expectedCode: 'ALL_DEPARTMENTS_APPROVED' | 'DEPARTMENT_REJECTIONS' | 'INCOMPLETE_REVIEWS';
  permit: Permit;
  events: TraceEvent[];
}

export const FIXTURE_SCENARIOS: Record<'approved' | 'rejected' | 'timeout', FixtureScenario> = {
  approved: {
    id: 'approved',
    title: 'Clean Approval (All 4 Departments)',
    subtitle: 'Single Family Residential Rear Addition',
    description: 'All 4 departments (Zoning, Fire, Water, Structural) evaluate and approve on first attempt.',
    expectedDecision: 'APPROVED',
    expectedCode: 'ALL_DEPARTMENTS_APPROVED',
    permit: permitApprovedJson as Permit,
    events: traceEventsApprovedJson as TraceEvent[],
  },
  rejected: {
    id: 'rejected',
    title: 'Department Rejection (Fire Violations)',
    subtitle: 'Commercial Restaurant Outdoor Dining',
    description: 'Fire department reports 2 violations (sprinkler missing & inadequate egress). Clean reject with aggregated reasons.',
    expectedDecision: 'REJECTED',
    expectedCode: 'DEPARTMENT_REJECTIONS',
    permit: permitRejectedFireJson as Permit,
    events: traceEventsRejectedJson as TraceEvent[],
  },
  timeout: {
    id: 'timeout',
    title: 'Fail-Safe Timeout / Retry (Fire Offline)',
    subtitle: 'Suburban Storage Warehouse Modernization',
    description: 'Fire department times out after 3 attempts with exponential backoffs (200ms, 500ms). Consensus refuses approval and rejects as incomplete.',
    expectedDecision: 'REJECTED',
    expectedCode: 'INCOMPLETE_REVIEWS',
    permit: permitTimeoutOfflineJson as Permit,
    events: traceEventsTimeoutJson as TraceEvent[],
  },
};
