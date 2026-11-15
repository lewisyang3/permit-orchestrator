import React from 'react';
import { ParsedSpan } from '../utils/traceParser';
import { formatDurationMs, formatTimestamp, getStatusColor } from '../utils/formatters';
import {
  X,
  Clock,
  Layers,
  AlertTriangle,
  RotateCw,
} from 'lucide-react';

interface SpanDetailDrawerProps {
  span: ParsedSpan | null;
  onClose: () => void;
}

export const SpanDetailDrawer: React.FC<SpanDetailDrawerProps> = ({ span, onClose }) => {
  if (!span) return null;

  const statusColors = getStatusColor(span.status);

  return (
    <aside className="fixed inset-y-0 right-0 w-full sm:w-[480px] bg-slate-900 border-l border-slate-800 shadow-2xl z-50 flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-slate-800 flex items-center justify-between bg-slate-950/60">
        <div>
          <div className="flex items-center space-x-2">
            <h3 className="text-base font-bold text-white tracking-tight">{span.label}</h3>
            <span
              className={`px-2 py-0.5 rounded text-[11px] font-mono font-semibold uppercase ${statusColors.bg} ${statusColors.text} border ${statusColors.border}`}
            >
              {span.status}
            </span>
          </div>
          <p className="text-xs text-slate-400 font-mono mt-0.5">span: {span.id}</p>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
        >
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Body Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-5 text-xs text-slate-300">
        {/* Timing Summary */}
        <div className="grid grid-cols-2 gap-2 bg-slate-950/60 p-3 rounded-xl border border-slate-800/80">
          <div>
            <span className="text-slate-500 block mb-0.5">Duration:</span>
            <span className="text-sm font-bold font-mono text-slate-200">
              {formatDurationMs(span.durationMs)}
            </span>
          </div>
          <div>
            <span className="text-slate-500 block mb-0.5">Start Offset:</span>
            <span className="text-sm font-bold font-mono text-slate-200">
              +{formatDurationMs(span.startOffsetMs)}
            </span>
          </div>
          <div className="col-span-2 pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px] text-slate-400">
            <span>Started: {formatTimestamp(span.events[0]?.ts)}</span>
            <span>Ended: {formatTimestamp(span.events[span.events.length - 1]?.ts)}</span>
          </div>
        </div>

        {/* Retry / Attempts Breakdown (if applicable) */}
        {span.attempts && span.attempts.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center space-x-1.5">
              <RotateCw className="h-3.5 w-3.5 text-sky-400" />
              <span>Retry Execution Flow ({span.attempts.length} Attempts)</span>
            </h4>
            <div className="space-y-2">
              {span.attempts.map((att) => (
                <div
                  key={att.attempt}
                  className={`p-3 rounded-lg border text-xs ${
                    att.status === 'timed_out'
                      ? 'bg-rose-950/20 border-rose-500/30'
                      : att.status === 'success'
                      ? 'bg-emerald-950/20 border-emerald-500/30'
                      : 'bg-slate-950/60 border-slate-800'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-bold text-slate-200">Attempt #{att.attempt}</span>
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-mono uppercase font-semibold ${
                        att.status === 'timed_out'
                          ? 'bg-rose-500/20 text-rose-300'
                          : att.status === 'success'
                          ? 'bg-emerald-500/20 text-emerald-300'
                          : 'bg-slate-800 text-slate-300'
                      }`}
                    >
                      {att.status}
                    </span>
                  </div>
                  {att.error && (
                    <p className="text-rose-300 text-[11px] font-mono mt-1">Error: {att.error}</p>
                  )}
                  {att.backoffMs && (
                    <div className="mt-1.5 pt-1.5 border-t border-slate-800/60 text-[11px] text-slate-400 flex items-center space-x-1">
                      <Clock className="h-3 w-3 text-sky-400" />
                      <span>Exponential backoff delay before next attempt: {att.backoffMs}ms</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Violations if present */}
        {span.violations && span.violations.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold text-amber-400 uppercase tracking-wider flex items-center space-x-1.5">
              <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
              <span>Rule Violations ({span.violations.length})</span>
            </h4>
            <div className="space-y-1.5">
              {span.violations.map((v, i) => (
                <div
                  key={i}
                  className="bg-slate-950/60 p-2.5 rounded-lg border border-amber-500/30 text-slate-200"
                >
                  <span className="font-mono text-amber-400 text-[11px] block mb-0.5">
                    {v.rule_id}
                  </span>
                  <p className="text-slate-300">{v.message}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Trace Events Chronology for this Span */}
        <div className="space-y-2">
          <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center space-x-1.5">
            <Layers className="h-3.5 w-3.5 text-indigo-400" />
            <span>Span Events Timeline ({span.events.length})</span>
          </h4>
          <div className="border border-slate-800 rounded-xl overflow-hidden divide-y divide-slate-800 bg-slate-950/40">
            {span.events.map((evt) => (
              <div key={evt.event_id} className="p-2.5 hover:bg-slate-900/60 transition-colors">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono font-semibold text-slate-200 text-[11px]">
                    {evt.type}
                  </span>
                  <span className="text-[10px] text-slate-500 font-mono">
                    {formatTimestamp(evt.ts)}
                  </span>
                </div>
                {evt.detail && Object.keys(evt.detail).length > 0 && (
                  <pre className="text-[10px] font-mono text-slate-400 bg-slate-950/80 p-1.5 rounded overflow-x-auto">
                    {JSON.stringify(evt.detail, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </aside>
  );
};
