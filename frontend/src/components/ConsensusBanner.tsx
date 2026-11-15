import React from 'react';
import { ParsedTrace } from '../utils/traceParser';
import { formatDurationMs } from '../utils/formatters';
import {
  CheckCircle2,
  XCircle,
  AlertOctagon,
  Clock,
  ShieldAlert,
  Hash,
  User,
  Building,
} from 'lucide-react';

interface ConsensusBannerProps {
  trace: ParsedTrace;
}

export const ConsensusBanner: React.FC<ConsensusBannerProps> = ({ trace }) => {
  const isApproved = trace.finalDecision === 'APPROVED';
  const isIncomplete = trace.decisionCode === 'INCOMPLETE_REVIEWS';

  return (
    <div
      className={`rounded-xl border p-5 transition-all shadow-sm ${
        isApproved
          ? 'bg-emerald-950/30 border-emerald-500/40'
          : isIncomplete
          ? 'bg-rose-950/30 border-rose-500/40'
          : 'bg-amber-950/30 border-amber-500/40'
      }`}
    >
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 pb-4 border-b border-slate-800">
        {/* Left: Decision Badge & Summary */}
        <div className="flex items-start space-x-3.5">
          <div
            className={`p-2.5 rounded-xl border mt-0.5 ${
              isApproved
                ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400'
                : isIncomplete
                ? 'bg-rose-500/15 border-rose-500/40 text-rose-400'
                : 'bg-amber-500/15 border-amber-500/40 text-amber-400'
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
                className={`text-lg font-bold tracking-tight uppercase ${
                  isApproved
                    ? 'text-emerald-400'
                    : isIncomplete
                    ? 'text-rose-400'
                    : 'text-amber-400'
                }`}
              >
                {trace.finalDecision || 'PROCESSING'}
              </span>

              {trace.decisionCode && (
                <span className="px-2.5 py-0.5 rounded text-xs font-mono font-semibold bg-slate-800/80 text-slate-300 border border-slate-700">
                  {trace.decisionCode}
                </span>
              )}

              <span className="flex items-center space-x-1 text-xs text-slate-400 bg-slate-900/60 px-2 py-0.5 rounded border border-slate-800">
                <Clock className="h-3 w-3 text-slate-400" />
                <span>Total Latency: {formatDurationMs(trace.totalDurationMs)}</span>
              </span>
            </div>

            <p className="text-sm text-slate-300 mt-1 font-medium leading-relaxed">
              {trace.decisionSummary || 'Evaluating consensus across all 4 departments...'}
            </p>
          </div>
        </div>

        {/* Right: Metadata Pills */}
        <div className="flex flex-col sm:flex-row md:flex-col lg:flex-row gap-2 text-xs">
          <div className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-slate-900/80 border border-slate-800 text-slate-300 font-mono">
            <Hash className="h-3.5 w-3.5 text-slate-500" />
            <span className="text-slate-500">Permit:</span>
            <span className="text-slate-200">{trace.permitId}</span>
          </div>
          <div className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-slate-900/80 border border-slate-800 text-slate-300 font-mono">
            <Hash className="h-3.5 w-3.5 text-slate-500" />
            <span className="text-slate-500">Trace:</span>
            <span className="text-slate-200">{trace.traceId}</span>
          </div>
        </div>
      </div>

      {/* Project & Applicant row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 pt-3 text-xs text-slate-300">
        {trace.projectTitle && (
          <div className="flex items-center space-x-2">
            <Building className="h-4 w-4 text-slate-500 shrink-0" />
            <span className="text-slate-400">Project:</span>
            <span className="font-medium text-slate-200 truncate">{trace.projectTitle}</span>
          </div>
        )}
        {trace.applicant && (
          <div className="flex items-center space-x-2">
            <User className="h-4 w-4 text-slate-500 shrink-0" />
            <span className="text-slate-400">Applicant:</span>
            <span className="font-medium text-slate-200">{trace.applicant}</span>
          </div>
        )}
        <div className="flex items-center space-x-2">
          <ShieldAlert className="h-4 w-4 text-emerald-400 shrink-0" />
          <span className="text-slate-400">Consensus Rule:</span>
          <span className="font-medium text-slate-200">
            {isIncomplete
              ? 'Refused: Missing Department Review'
              : isApproved
              ? 'Consensus: 4/4 Signed Off'
              : 'Rejected: Code Violations'}
          </span>
        </div>
      </div>

      {/* Violations section if present */}
      {trace.violations.length > 0 && (
        <div className="mt-4 pt-3 border-t border-slate-800/80">
          <h4 className="text-xs font-semibold text-amber-400 uppercase tracking-wider mb-2 flex items-center space-x-1.5">
            <XCircle className="h-3.5 w-3.5" />
            <span>Violations Cited ({trace.violations.length})</span>
          </h4>
          <div className="space-y-1.5">
            {trace.violations.map((v, idx) => (
              <div
                key={idx}
                className="text-xs bg-slate-900/80 rounded-lg p-2.5 border border-amber-500/20 text-slate-200 flex items-start space-x-2"
              >
                <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-mono text-[10px] uppercase font-semibold shrink-0">
                  {v.department}
                </span>
                <span className="font-mono text-slate-400 text-[11px] shrink-0">{v.rule_id}:</span>
                <span className="text-slate-300">{v.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Timeout / Retry notice if incomplete */}
      {isIncomplete && trace.failedDepartments.length > 0 && (
        <div className="mt-4 pt-3 border-t border-slate-800/80">
          <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-lg text-xs text-rose-300 flex items-start space-x-2">
            <AlertOctagon className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-rose-200 mb-0.5">
                Fail-Safe Consensus Guarantee (Goal #1)
              </p>
              <p className="text-rose-300/90 leading-relaxed">
                The consensus engine refused approval because department{' '}
                <span className="font-mono font-bold text-rose-200">
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
