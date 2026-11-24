import { describe, it, expect } from 'vitest';
import { evaluatePermitData } from '../utils/permitEvaluator';
import {
  RESIDENTIAL_ADDITION_PERMIT,
  COMMERCIAL_RESTAURANT_PERMIT,
} from '../fixtures/samplePermits';
import { DepartmentFinding, PermitViolation } from '../types';

const findingsOf = (permit = COMMERCIAL_RESTAURANT_PERMIT): DepartmentFinding[] =>
  evaluatePermitData(permit)
    .filter((e) => e.type === 'CALL_SUCCEEDED' || e.type === 'CALL_FAILED')
    .flatMap((e) => (e.detail?.findings as DepartmentFinding[]) ?? []);

describe('local permit evaluator', () => {
  it('claims no jurisdiction and cites no municipal code', () => {
    for (const permit of [RESIDENTIAL_ADDITION_PERMIT, COMMERCIAL_RESTAURANT_PERMIT]) {
      const events = evaluatePermitData(permit);
      for (const e of events) {
        expect(e.detail?.jurisdiction_id).toBeUndefined();
      }
      const text = JSON.stringify(events);
      expect(text).not.toMatch(/San Jos[eé] municipal code|municode|§/i);
      for (const f of findingsOf(permit)) {
        expect(f.rule_sources).toBeUndefined();
        expect(f.message).toMatch(/local preview/i);
      }
    }
  });

  it('keeps the sample verdicts: residential approved, commercial rejected', () => {
    const decided = (p: typeof RESIDENTIAL_ADDITION_PERMIT) =>
      evaluatePermitData(p).find((e) => e.type === 'PERMIT_DECIDED')?.detail?.final_decision;
    expect(decided(RESIDENTIAL_ADDITION_PERMIT)).toBe('APPROVED');
    expect(decided(COMMERCIAL_RESTAURANT_PERMIT)).toBe('REJECTED');
  });

  it('attaches the evidence each check read to its violation', () => {
    const decided = evaluatePermitData(COMMERCIAL_RESTAURANT_PERMIT).find(
      (e) => e.type === 'PERMIT_DECIDED',
    );
    const violations = decided?.detail?.violations as PermitViolation[];
    const sprinkler = violations.find((v) => v.rule_id === 'SJ-F-201');
    expect(sprinkler?.evidence?.map((s) => s.field_path)).toEqual([
      'fire.occupancy_type',
      'fire.has_sprinkler_system',
      'fire.occupant_load',
    ]);

    const failed = findingsOf().filter((f) => !f.passed);
    expect(failed.map((f) => f.rule_id)).toEqual(violations.map((v) => v.rule_id));
  });
});
