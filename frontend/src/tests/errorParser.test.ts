import { describe, it, expect } from 'vitest';
import { parsePlanError, groupMissingFields, formatField } from '../utils/errorParser';

describe('errorParser utilities', () => {
  it('parses raw 422 JSON with MISSING_REQUIRED_FIELDS into clean error', () => {
    const rawError = `Plan parsing failed (422): {"detail":{"error":"MISSING_REQUIRED_FIELDS","message":"Plan parsing failed. Missing required fields: applicant.email, applicant.name","missing_fields":["applicant.email","applicant.name","fire.occupancy_type"]}}`;
    const parsed = parsePlanError(new Error(rawError));

    expect(parsed.title).toBe('Plan Incomplete: Missing Required Fields');
    expect(parsed.message).toContain('missing 3 required municipal parameters');
    expect(parsed.missingFields).toEqual(['applicant.email', 'applicant.name', 'fire.occupancy_type']);
  });

  it('parses 422 INVALID_FIELD_VALUES into clean error', () => {
    const rawError = `Plan parsing failed (422): {"detail":{"error":"INVALID_FIELD_VALUES","message":"Invalid parameters found","errors":["zoning.building_height_ft must be positive"]}}`;
    const parsed = parsePlanError(new Error(rawError));

    expect(parsed.title).toBe('Invalid Plan Parameters');
    expect(parsed.invalidFields).toEqual(['zoning.building_height_ft must be positive']);
  });

  it('cleans plain HTTP 400 string error', () => {
    const rawError = `Plan parsing failed (400): {"detail":"Plan parsing failed: No readable text could be extracted from plan.pdf."}`;
    const parsed = parsePlanError(new Error(rawError));

    expect(parsed.title).toBe('Plan Parsing Failed');
    expect(parsed.message).toBe('No readable text could be extracted from plan.pdf.');
  });

  it('preserves generic error messages', () => {
    const parsed = parsePlanError(new Error('Network error loading sample plan'));
    expect(parsed.title).toBe('Plan Parsing Failed');
    expect(parsed.message).toBe('Network error loading sample plan');
  });

  it('groups missing fields into appropriate departments', () => {
    const fields = [
      'applicant.name',
      'applicant.email',
      'zoning.setback_front_ft',
      'fire.occupancy_type',
      'water.daily_water_usage_gpd',
      'structural.stories_count',
    ];
    const grouped = groupMissingFields(fields);

    expect(grouped['Applicant']).toHaveLength(2);
    expect(grouped['Zoning & Location']).toHaveLength(1);
    expect(grouped['Fire Life Safety']).toHaveLength(1);
    expect(grouped['Water & Utilities']).toHaveLength(1);
    expect(grouped['Structural Engineering']).toHaveLength(1);

    expect(grouped['Applicant'][0].label).toBe('Applicant Name');
    expect(grouped['Zoning & Location'][0].label).toBe('Front Setback (ft)');
  });

  it('formats unknown field paths with fallback title casing', () => {
    const formatted = formatField('custom_dept.my_custom_parameter');
    expect(formatted.label).toBe('My Custom Parameter');
    expect(formatted.category).toBe('General');
  });
});
