import React from 'react';
import { ExternalLink } from 'lucide-react';
import { EvidenceSpan, RuleSource } from '../types';
import { EvidenceChip } from './EvidenceChip';

interface FindingEvidenceProps {
  evidence?: EvidenceSpan[];
  ruleSources?: RuleSource[];
}

function sourceLabel(src: RuleSource): string {
  const parts = [src.section, src.printed_page != null ? `printed p.${src.printed_page}` : null].filter(Boolean);
  return parts.length ? parts.join(', ') : src.url ?? 'Source';
}

/** The two sides of a finding: what the plan says, and the city rule it was checked against. */
export const FindingEvidence: React.FC<FindingEvidenceProps> = ({ evidence, ruleSources }) => {
  const plan = evidence ?? [];
  const sources = ruleSources ?? [];
  if (plan.length === 0 && sources.length === 0) return null;

  return (
    <div className="mt-2.5 pt-2.5 border-t border-black/[0.06] space-y-2 text-[11px]">
      {plan.length > 0 && (
        <div>
          <span className="block text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1">Plan says</span>
          <ul className="space-y-1">
            {plan.map((span) => (
              <li key={span.field_path} className="flex items-start gap-1.5">
                <span className="font-mono text-slate-700 break-words min-w-0">“{span.quote}”</span>
                <EvidenceChip span={span} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {sources.length > 0 && (
        <div>
          <span className="block text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1">City rule</span>
          <ul className="space-y-1">
            {sources.map((src, i) => (
              <li key={i} className="text-slate-700">
                {src.url ? (
                  <a
                    href={src.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-center gap-1 text-[#0071e3] hover:underline"
                  >
                    <span>{sourceLabel(src)}</span>
                    <ExternalLink className="h-3 w-3 shrink-0" />
                  </a>
                ) : (
                  <span>{sourceLabel(src)}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
