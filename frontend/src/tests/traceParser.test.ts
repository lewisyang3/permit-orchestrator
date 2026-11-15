import { describe, it, expect } from 'vitest';
import { parseTraceEvents } from '../utils/traceParser';
import { FIXTURE_SCENARIOS } from '../fixtures';

describe('traceParser with Golden Fixtures', () => {
  it('correctly parses approved fixture', () => {
    const trace = parseTraceEvents(FIXTURE_SCENARIOS.approved.events);
    expect(trace.traceId).toBe('trc-app-001');
    expect(trace.permitId).toBe('pm-fixture-approved-001');
    expect(trace.finalDecision).toBe('APPROVED');
    expect(trace.decisionCode).toBe('ALL_DEPARTMENTS_APPROVED');
    expect(trace.isComplete).toBe(true);

    const spans = trace.spans;
    expect(spans.length).toBeGreaterThanOrEqual(6); // orchestrator, 4 depts, consensus

    const zoning = spans.find((s) => s.id === 'dept:zoning');
    expect(zoning).toBeDefined();
    expect(zoning?.status).toBe('APPROVED');

    const fire = spans.find((s) => s.id === 'dept:fire');
    expect(fire?.status).toBe('APPROVED');
  });

  it('correctly parses rejected fixture with fire violations', () => {
    const trace = parseTraceEvents(FIXTURE_SCENARIOS.rejected.events);
    expect(trace.traceId).toBe('trc-rej-002');
    expect(trace.permitId).toBe('pm-fixture-rejected-fire-002');
    expect(trace.finalDecision).toBe('REJECTED');
    expect(trace.decisionCode).toBe('DEPARTMENT_REJECTIONS');
    expect(trace.isComplete).toBe(true);

    const fire = trace.spans.find((s) => s.id === 'dept:fire');
    expect(fire?.status).toBe('REJECTED');
    expect(fire?.violations.length).toBe(2);
  });

  it('correctly parses timeout fixture with retry attempts', () => {
    const trace = parseTraceEvents(FIXTURE_SCENARIOS.timeout.events);
    expect(trace.traceId).toBe('trc-to-003');
    expect(trace.permitId).toBe('pm-fixture-timeout-offline-003');
    expect(trace.finalDecision).toBe('REJECTED');
    expect(trace.decisionCode).toBe('INCOMPLETE_REVIEWS');
    expect(trace.isComplete).toBe(true);
    expect(trace.failedDepartments).toContain('fire');

    const fire = trace.spans.find((s) => s.id === 'dept:fire');
    expect(fire?.status).toBe('TIMEOUT');
    expect(fire?.attemptsCount).toBe(3);
    expect(fire?.attempts.length).toBe(3);
    expect(fire?.attempts[0].status).toBe('retrying');
    expect(fire?.attempts[0].backoffMs).toBe(200);
    expect(fire?.attempts[1].status).toBe('retrying');
    expect(fire?.attempts[1].backoffMs).toBe(500);
    expect(fire?.attempts[2].status).toBe('failed');
    expect(fire?.durationMs).toBeGreaterThan(6000);
  });
});
