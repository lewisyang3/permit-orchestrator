import { describe, it, expect } from 'vitest';
import {
  SAMPLE_PERMITS,
  SAMPLE_DOCUMENT_FILENAMES,
  sampleIntakeResult,
} from '../fixtures/samplePermits';
import { FIXTURE_SCENARIOS } from '../fixtures';
import { Permit } from '../types';

function getPath(permit: Permit, path: string): unknown {
  return path
    .split('.')
    .reduce<unknown>((obj, key) => (obj as Record<string, unknown> | undefined)?.[key], permit);
}

/** Does the quote state this value? Numbers compare as numbers, ignoring thousands separators. */
function quoteStates(quote: string, value: unknown): boolean {
  if (typeof value === 'number') {
    const numbers = (quote.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
    return numbers.includes(value);
  }
  return quote.toLowerCase().includes(String(value).toLowerCase());
}

describe('built-in sample permits', () => {
  for (const permit of SAMPLE_PERMITS) {
    describe(permit.project_title, () => {
      const spans = Object.entries(permit.evidence ?? {});

      it('has evidence', () => {
        expect(spans.length).toBeGreaterThan(0);
      });

      it.each(spans.filter(([, s]) => s.value_supported === true))(
        'quote for %s states the permit value',
        (path, span) => {
          expect(span.field_path).toBe(path);
          const value = getPath(permit, path);
          expect(value, `${path} has no value on the permit`).not.toBeUndefined();
          expect(
            quoteStates(span.quote, value),
            `"${span.quote}" does not state ${path} = ${JSON.stringify(value)}`,
          ).toBe(true);
        },
      );

      it('names a known sample document', () => {
        expect(SAMPLE_DOCUMENT_FILENAMES[permit.source_document_id ?? '']).toBeDefined();
      });
    });
  }

  it('labels sample intake results as built-in, never as a model extraction', () => {
    for (const permit of SAMPLE_PERMITS) {
      const result = sampleIntakeResult(permit);
      expect(result?.extracted_by).toBe('built-in-fixture-fallback');
      expect(result?.filename).toBe(SAMPLE_DOCUMENT_FILENAMES[permit.source_document_id!]);
    }
  });

  it('returns no intake result for permits that are not built-in samples', () => {
    expect(sampleIntakeResult(FIXTURE_SCENARIOS.approved.permit)).toBeNull();
    expect(
      sampleIntakeResult({ ...SAMPLE_PERMITS[0], source_document_id: 'pm-intake-0123abcd' }),
    ).toBeNull();
  });
});
