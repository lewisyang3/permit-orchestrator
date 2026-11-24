import React from 'react';
import { ParsedTrace } from '../utils/traceParser';
import { formatDurationMs } from '../utils/formatters';
import {
  CheckCircle2,
  XCircle,
  AlertOctagon,
  Clock,
  ShieldCheck,
  Hash,
  User,
  Building,
  ArrowRight,
} from 'lucide-react';
import { FindingEvidence } from './FindingEvidence';

export interface ConsensusBannerProps {
  trace: ParsedTrace;
  onViewTrace?: () => void;
}

export const ConsensusBanner: React.FC<ConsensusBannerProps> = ({ trace, onViewTrace }) => {
  const isApproved = trace.finalDecision === 'APPROVED';
  const isIncomplete = trace.decisionCode === 'INCOMPLETE_REVIEWS';

  return (
    <div
      className={`rounded-3xl border p-5 sm:p-6 transition-all shadow-xs bg-white ${
        isApproved
          ? 'border-emerald-200'
          : isIncomplete
          ? 'border-rose-200'
          : 'border-amber-200'
      }`}
    >
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 pb-4 border-b border-slate-100">
        {/* Left: Decision Badge & Summary */}
        <div className="flex items-start space-x-3.5">
          <div
            className={`p-3 rounded-2xl shrink-0 mt-0.5 ${
              isApproved
                ? 'bg-emerald-50 text-emerald-600 border border-emerald-200'
                : isIncomplete
                ? 'bg-rose-50 text-rose-600 border border-rose-200'
                : 'bg-amber-50 text-amber-600 border border-amber-200'
            }`}
          >
            {isApproved ? (
              <CheckCircle2 className="h-7 w-7" />
            ) : isIncomplete ? (
              <AlertOctagon className="h-7 w-7" />
            ) : (
              <XCircle className="h-7 w-7" />
            )}
          </div>

          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`text-lg sm:text-xl font-bold tracking-tight uppercase ${
                  isApproved
                    ? 'text-emerald-700'
                    : isIncomplete
                    ? 'text-rose-700'
                    : 'text-amber-700'
                }`}
              >
                {trace.finalDecision || 'PROCESSING'}
              </span>

              {trace.decisionCode && (
                <span className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-slate-100 text-slate-800 border border-slate-200">
                  {trace.decisionCode}
                </span>
              )}

              <span className="flex items-center space-x-1 text-xs text-slate-600 bg-slate-50 px-2.5 py-1 rounded-full border border-slate-200 font-medium">
                <Clock className="h-3.5 w-3.5 text-slate-500" />
                <span>Total Latency: {formatDurationMs(trace.totalDurationMs)}</span>
              </span>
            </div>

            <p className="text-sm text-slate-700 mt-1.5 font-medium leading-relaxed">
              {trace.decisionSummary || 'Evaluating consensus across all 4 departments...'}
            </p>
          </div>
        </div>

        {/* Right: Metadata Pills & Quick Trace Jump */}
        <div className="flex flex-col sm:flex-row md:flex-col lg:flex-row gap-2 text-xs w-full md:w-auto justify-end">
          <div className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-slate-50 border border-slate-200 text-slate-700 font-mono">
            <Hash className="h-3.5 w-3.5 text-slate-400" />
            <span className="text-slate-500">Permit:</span>
            <span className="font-semibold text-[#1d1d1f]">{trace.permitId}</span>
          </div>
          <div className="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl bg-slate-50 border border-slate-200 text-slate-700 font-mono">
            <Hash className="h-3.5 w-3.5 text-slate-400" />
            <span className="text-slate-500">Trace:</span>
            <span className="font-semibold text-[#1d1d1f]">{trace.traceId}</span>
          </div>

          {onViewTrace && (
            <button
              type="button"
              onClick={onViewTrace}
              className="apple-press flex items-center justify-center space-x-1.5 px-3.5 py-1.5 rounded-xl bg-blue-50 hover:bg-blue-100 text-[#0071e3] font-semibold border border-blue-200 transition-colors shadow-2xs"
            >
              <span>Inspect Trace</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Project & Applicant Details Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 pt-3.5 text-xs text-slate-600">
        {trace.projectTitle && (
          <div className="flex items-center space-x-2">
            <Building className="h-4 w-4 text-slate-400 shrink-0" />
            <span className="text-slate-500">Project:</span>
            <span className="font-semibold text-[#1d1d1f] truncate">{trace.projectTitle}</span>
          </div>
        )}
        {trace.applicant && (
          <div className="flex items-center space-x-2">
            <User className="h-4 w-4 text-slate-400 shrink-0" />
            <span className="text-slate-500">Applicant:</span>
            <span className="font-semibold text-[#1d1d1f]">{trace.applicant}</span>
          </div>
        )}
        <div className="flex items-center space-x-2">
          <ShieldCheck className="h-4 w-4 text-emerald-600 shrink-0" />
          <span className="text-slate-500">Consensus Rule:</span>
          <span className="font-semibold text-[#1d1d1f]">
            {isIncomplete
              ? 'Refused: Missing Department Review'
              : isApproved
              ? '4/4 Signed Off'
              : 'Rejected: Code Violations'}
          </span>
        </div>
      </div>

      {/* Violations section if present */}
      {trace.violations.length > 0 && (
        <div className="mt-4 pt-3.5 border-t border-slate-100">
          <h4 className="text-xs font-bold text-amber-800 uppercase tracking-wider mb-2.5 flex items-center space-x-1.5">
            <XCircle className="h-3.5 w-3.5 text-amber-600" />
            <span>Violations Cited ({trace.violations.length})</span>
          </h4>
          <div className="space-y-2">
            {trace.violations.map((v, idx) => (
              <div
                key={idx}
                className="text-xs bg-amber-50/50 rounded-2xl p-3 border border-amber-200/70 text-slate-800 flex items-start space-x-2.5"
              >
                <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 font-mono text-[10px] uppercase font-bold shrink-0 border border-amber-200">
                  {v.department}
                </span>
                <span className="font-mono text-slate-500 text-[11px] font-semibold shrink-0">
                  {v.rule_id}:
                </span>
                <div className="min-w-0">
                  <span className="text-slate-700 font-medium">{v.message}</span>
                  <FindingEvidence evidence={v.evidence} ruleSources={v.rule_sources} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Timeout / Retry notice if incomplete */}
      {isIncomplete && trace.failedDepartments.length > 0 && (
        <div className="mt-4 pt-3.5 border-t border-slate-100">
          <div className="p-4 bg-rose-50 border border-rose-200 rounded-2xl text-xs text-rose-900 flex items-start space-x-3">
            <AlertOctagon className="h-5 w-5 text-rose-600 shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-rose-950 mb-1 text-sm">
                Fail-Safe Consensus Guarantee (Goal #1)
              </p>
              <p className="text-rose-900/90 leading-relaxed font-normal">
                The consensus engine refused approval because department{' '}
                <span className="font-mono font-bold text-rose-950 underline decoration-rose-400">
                  [{trace.failedDepartments.join(', ')}]
                </span>{' '}
                did not complete sign-off after all retries timed out (2.0s per request). The permit
                failed cleanly and did not hang.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
