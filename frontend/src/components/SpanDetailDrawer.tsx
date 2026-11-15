import React from 'react';
import { ParsedSpan } from '../utils/traceParser';
import { formatDurationMs, formatTimestamp } from '../utils/formatters';
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

  return (
    <>
      {/* Translucent Dimming Scrim Backdrop */}
      <div
        onClick={onClose}
        className="fixed inset-0 bg-black/30 backdrop-blur-xs z-40 transition-opacity"
      />

      {/* Slide-over Apple Sheet */}
      <aside className="fixed inset-y-0 right-0 w-full sm:w-[500px] bg-white border-l border-slate-200 shadow-2xl z-50 flex flex-col text-slate-800">
        {/* Header */}
        <div className="p-4 sm:p-5 border-b border-slate-200 flex items-center justify-between bg-slate-50/80">
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="text-base font-bold text-[#1d1d1f] tracking-tight">{span.label}</h3>
              <span
                className={`px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold uppercase ${
                  span.status === 'APPROVED'
                    ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
                    : span.status === 'TIMEOUT'
                    ? 'bg-rose-50 text-rose-800 border border-rose-200'
                    : span.status === 'REJECTED'
                    ? 'bg-amber-50 text-amber-800 border border-amber-200'
                    : 'bg-blue-50 text-blue-800 border border-blue-200'
                }`}
              >
                {span.status}
              </span>
            </div>
            <p className="text-xs text-slate-400 font-mono mt-0.5">span: {span.id}</p>
          </div>
          <button
            onClick={onClose}
            className="apple-press p-2 rounded-xl text-slate-400 hover:text-[#1d1d1f] hover:bg-slate-200/80 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body Content */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-5 text-xs text-slate-700">
          {/* Timing Summary */}
          <div className="grid grid-cols-2 gap-3 bg-slate-50/80 p-4 rounded-2xl border border-slate-200">
            <div>
              <span className="text-slate-500 block mb-1">Duration:</span>
              <span className="text-sm font-bold font-mono text-[#1d1d1f]">
                {formatDurationMs(span.durationMs)}
              </span>
            </div>
            <div>
              <span className="text-slate-500 block mb-1">Start Offset:</span>
              <span className="text-sm font-bold font-mono text-[#1d1d1f]">
                +{formatDurationMs(span.startOffsetMs)}
              </span>
            </div>
            <div className="col-span-2 pt-2.5 border-t border-slate-200 flex items-center justify-between text-[11px] text-slate-500">
              <span>Started: {formatTimestamp(span.events[0]?.ts)}</span>
              <span>Ended: {formatTimestamp(span.events[span.events.length - 1]?.ts)}</span>
            </div>
          </div>

          {/* Retry / Attempts Breakdown (if applicable) */}
          {span.attempts && span.attempts.length > 0 && (
            <div className="space-y-2.5">
              <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider flex items-center space-x-1.5">
                <RotateCw className="h-3.5 w-3.5 text-blue-600" />
                <span>Retry Execution Flow ({span.attempts.length} Attempts)</span>
              </h4>
              <div className="space-y-2.5">
                {span.attempts.map((att) => (
                  <div
                    key={att.attempt}
                    className={`p-3.5 rounded-2xl border text-xs transition-all ${
                      att.status === 'timed_out'
                        ? 'bg-rose-50/50 border-rose-200'
                        : att.status === 'success'
                        ? 'bg-emerald-50/50 border-emerald-200'
                        : 'bg-slate-50 border-slate-200'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-bold text-[#1d1d1f]">Attempt #{att.attempt}</span>
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-mono uppercase font-bold ${
                          att.status === 'timed_out'
                            ? 'bg-rose-100 text-rose-800'
                            : att.status === 'success'
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-slate-200 text-slate-800'
                        }`}
                      >
                        {att.status}
                      </span>
                    </div>
                    {att.error && (
                      <p className="text-rose-700 text-[11px] font-mono mt-1 font-medium">Error: {att.error}</p>
                    )}
                    {att.backoffMs && (
                      <div className="mt-2 pt-2 border-t border-slate-200 text-[11px] text-slate-600 flex items-center space-x-1.5">
                        <Clock className="h-3 w-3 text-blue-600" />
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
            <div className="space-y-2.5">
              <h4 className="text-xs font-bold text-amber-800 uppercase tracking-wider flex items-center space-x-1.5">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                <span>Rule Violations ({span.violations.length})</span>
              </h4>
              <div className="space-y-2">
                {span.violations.map((v, i) => (
                  <div
                    key={i}
                    className="bg-amber-50/50 p-3.5 rounded-2xl border border-amber-200 text-slate-800 shadow-xs"
                  >
                    <span className="font-mono text-amber-800 text-[11px] font-bold block mb-1">
                      {v.rule_id}
                    </span>
                    <p className="text-slate-800 leading-relaxed font-medium">{v.message}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Trace Events Chronology for this Span */}
          <div className="space-y-2.5">
            <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider flex items-center space-x-1.5">
              <Layers className="h-3.5 w-3.5 text-indigo-600" />
              <span>Span Events Timeline ({span.events.length})</span>
            </h4>
            <div className="border border-slate-200 rounded-2xl overflow-hidden divide-y divide-slate-100 bg-slate-50/50">
              {span.events.map((evt) => (
                <div key={evt.event_id} className="p-3 hover:bg-white transition-colors">
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono font-bold text-[#1d1d1f] text-[11px]">
                      {evt.type}
                    </span>
                    <span className="text-[10px] text-slate-500 font-mono">
                      {formatTimestamp(evt.ts)}
                    </span>
                  </div>
                  {evt.detail && Object.keys(evt.detail).length > 0 && (
                    <pre className="text-[10px] font-mono text-slate-700 bg-white p-2.5 rounded-xl overflow-x-auto border border-slate-200">
                      {JSON.stringify(evt.detail, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </aside>
    </>
  );
};
