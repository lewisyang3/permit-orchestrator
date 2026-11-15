import React from 'react';
import { ParsedTrace, ParsedSpan } from '../utils/traceParser';
import { SpanBar } from './SpanBar';
import { formatDurationMs, getStatusColor } from '../utils/formatters';
import {
  Workflow,
  Server,
  Scale,
  RotateCw,
  ChevronRight,
} from 'lucide-react';

interface TraceWaterfallProps {
  trace: ParsedTrace;
  selectedSpan: ParsedSpan | null;
  onSelectSpan: (span: ParsedSpan) => void;
}

export const TraceWaterfall: React.FC<TraceWaterfallProps> = ({
  trace,
  selectedSpan,
  onSelectSpan,
}) => {
  const totalMs = Math.max(1, trace.totalDurationMs);

  // Generate 5 ruler ticks
  const ticksCount = 5;
  const rulerTicks = Array.from({ length: ticksCount + 1 }, (_, i) => {
    const ms = (totalMs / ticksCount) * i;
    const percent = (i / ticksCount) * 100;
    return { ms, percent };
  });

  const getSpanIcon = (spanId: string) => {
    if (spanId === 'orchestrator') return <Workflow className="h-4 w-4 text-emerald-400" />;
    if (spanId === 'consensus') return <Scale className="h-4 w-4 text-purple-400" />;
    return <Server className="h-4 w-4 text-sky-400" />;
  };

  return (
    <div className="bg-slate-900/90 rounded-xl border border-slate-800 shadow-xl overflow-hidden">
      {/* Waterfall Table Header */}
      <div className="grid grid-cols-12 border-b border-slate-800 bg-slate-950/70 text-xs font-semibold text-slate-400 px-4 py-3">
        <div className="col-span-12 md:col-span-4 flex items-center justify-between pr-4">
          <span>Span / Component</span>
          <span>Status &amp; Attempts</span>
        </div>
        <div className="hidden md:block md:col-span-8 pl-4 relative">
          <div className="flex justify-between text-[11px] font-mono text-slate-400">
            {rulerTicks.map((t, idx) => (
              <span key={idx} style={{ left: `${t.percent}%` }}>
                {formatDurationMs(t.ms)}
              </span>
            ))}
          </div>
          {/* Subtle grid ticks line */}
          <div className="flex justify-between h-1.5 mt-1 border-b border-slate-800">
            {rulerTicks.map((_, idx) => (
              <div key={idx} className="w-px h-full bg-slate-700" />
            ))}
          </div>
        </div>
      </div>

      {/* Waterfall Rows */}
      <div className="divide-y divide-slate-800/60">
        {trace.spans.map((span) => {
          const isSelected = selectedSpan?.id === span.id;
          const statusColors = getStatusColor(span.status);
          const isChild = span.id !== 'orchestrator';

          return (
            <div
              key={span.id}
              className={`grid grid-cols-12 px-4 py-2.5 items-center transition-colors group ${
                isSelected ? 'bg-slate-800/60' : 'hover:bg-slate-850/50'
              }`}
            >
              {/* Left Column: Span metadata */}
              <div
                onClick={() => onSelectSpan(span)}
                className="col-span-12 md:col-span-4 flex items-center justify-between pr-4 cursor-pointer"
              >
                <div className={`flex items-center space-x-2 truncate ${isChild ? 'pl-4' : ''}`}>
                  {isChild && <ChevronRight className="h-3 w-3 text-slate-600 shrink-0" />}
                  <span className="shrink-0">{getSpanIcon(span.id)}</span>
                  <div className="truncate">
                    <span className="text-xs font-semibold text-slate-200 block truncate group-hover:text-white">
                      {span.label}
                    </span>
                    <span className="text-[10px] text-slate-500 font-mono block truncate">
                      {span.id}
                    </span>
                  </div>
                </div>

                <div className="flex items-center space-x-2 shrink-0">
                  {span.attemptsCount > 1 && (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-500/15 text-rose-300 border border-rose-500/30 flex items-center space-x-1">
                      <RotateCw className="h-2.5 w-2.5 animate-spin" />
                      <span>{span.attemptsCount}x</span>
                    </span>
                  )}

                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase tracking-wider ${statusColors.bg} ${statusColors.text} border ${statusColors.border}`}
                  >
                    {span.status}
                  </span>
                </div>
              </div>

              {/* Right Column: Waterfall Bar & Retry markers */}
              <div className="col-span-12 md:col-span-8 pl-0 md:pl-4 mt-2 md:mt-0">
                <SpanBar
                  span={span}
                  totalDurationMs={totalMs}
                  isSelected={isSelected}
                  onClick={() => onSelectSpan(span)}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer Info */}
      <div className="px-4 py-3 bg-slate-950/60 border-t border-slate-800 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
        <div className="flex items-center space-x-2">
          <span className="text-slate-500">Total Spans:</span>
          <span className="font-mono text-slate-200 font-semibold">{trace.spans.length}</span>
          <span className="text-slate-600">•</span>
          <span className="text-slate-500">Total Duration:</span>
          <span className="font-mono text-emerald-400 font-semibold">
            {formatDurationMs(trace.totalDurationMs)}
          </span>
        </div>

        <div className="flex items-center space-x-3 text-[11px]">
          <span className="flex items-center space-x-1">
            <span className="h-2 w-2 rounded-full bg-emerald-500 inline-block" />
            <span>Approved</span>
          </span>
          <span className="flex items-center space-x-1">
            <span className="h-2 w-2 rounded-full bg-amber-500 inline-block" />
            <span>Rejected</span>
          </span>
          <span className="flex items-center space-x-1">
            <span className="h-2 w-2 rounded-full bg-rose-500 inline-block" />
            <span>Timeout / Retry</span>
          </span>
          <span className="text-slate-600">•</span>
          <span className="text-slate-400">Click any span for attempt diagnostics</span>
        </div>
      </div>
    </div>
  );
};
