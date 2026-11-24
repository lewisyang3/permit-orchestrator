import { EvidenceSpan, Permit } from '../types';

/** How much a citation can be trusted, from the worker's point of view. */
export type EvidenceTone = 'verified' | 'mismatch' | 'unverified' | 'none';

export function evidenceTone(span: EvidenceSpan | undefined | null): EvidenceTone {
  if (!span) return 'none';
  if (span.status !== 'verified') return 'unverified';
  if (span.value_supported === false) return 'mismatch';
  return 'verified';
}

export const TONE_LABEL: Record<EvidenceTone, string> = {
  verified: 'Found in the document',
  mismatch: 'Found, but the text does not state this value',
  unverified: 'Not found in the document; check by hand',
  none: 'No source cited',
};

/** Short location label: "p.3", "line 12", or "?" when unknown. */
export function locationLabel(span: EvidenceSpan): string {
  if (span.page) return `p.${span.page}`;
  if (span.line) return `line ${span.line}`;
  return '?';
}

export function evidenceFor(permit: Permit, path: string): EvidenceSpan | undefined {
  return permit.evidence?.[path];
}

export interface EvidenceSummary {
  total: number;
  verified: number;
  mismatch: number;
  unverified: number;
}

export function summarizeEvidence(permit: Permit): EvidenceSummary | null {
  const spans = Object.values(permit.evidence ?? {});
  if (spans.length === 0) return null;
  const tones = spans.map(evidenceTone);
  return {
    total: spans.length,
    verified: tones.filter((t) => t === 'verified').length,
    mismatch: tones.filter((t) => t === 'mismatch').length,
    unverified: tones.filter((t) => t === 'unverified').length,
  };
}
