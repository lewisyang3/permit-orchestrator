import React from 'react';
import { EvidenceSpan } from '../types';
import { evidenceTone, locationLabel, TONE_LABEL, EvidenceTone } from '../utils/evidence';
import { useEvidence } from './EvidenceContext';

const TONE_CLASS: Record<EvidenceTone, string> = {
  verified: 'bg-emerald-50 text-emerald-800 border-emerald-200 hover:bg-emerald-100',
  mismatch: 'bg-rose-50 text-rose-800 border-rose-200 hover:bg-rose-100',
  unverified: 'bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100',
  none: 'bg-slate-50 text-slate-500 border-slate-200',
};

interface EvidenceChipProps {
  span: EvidenceSpan | undefined | null;
  /** Hide the chip entirely when there's no citation (e.g. fixture permits). */
  hideWhenMissing?: boolean;
}

/** Small "p.3" badge for one citation; click to see it highlighted in the document. */
export const EvidenceChip: React.FC<EvidenceChipProps> = ({ span, hideWhenMissing = true }) => {
  const { openEvidence } = useEvidence();
  const tone = evidenceTone(span);
  if (!span) {
    if (hideWhenMissing) return null;
    return (
      <span
        className={`ml-1.5 inline-flex px-1.5 py-px rounded-md border text-[10px] font-mono ${TONE_CLASS.none}`}
        title={TONE_LABEL.none}
      >
        no source
      </span>
    );
  }
  const title = `${TONE_LABEL[tone]}: "${span.quote}"`;
  const label = `${locationLabel(span)}${tone === 'verified' ? '' : tone === 'mismatch' ? ' ≠' : ' ?'}`;
  const className = `ml-1.5 inline-flex items-center px-1.5 py-px rounded-md border text-[10px] font-mono font-semibold align-middle transition-colors ${TONE_CLASS[tone]}`;

  if (!openEvidence) {
    return (
      <span className={className} title={title} data-testid={`evidence-${span.field_path}`}>
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      className={`apple-press ${className}`}
      title={title}
      aria-label={`Show source for ${span.field_path}: ${TONE_LABEL[tone]}`}
      data-testid={`evidence-${span.field_path}`}
      onClick={(e) => {
        e.stopPropagation();
        openEvidence(span);
      }}
    >
      {label}
    </button>
  );
};
