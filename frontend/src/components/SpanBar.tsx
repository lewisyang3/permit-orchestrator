import React from 'react';
import { ParsedSpan } from '../utils/traceParser';
import { formatDurationMs } from '../utils/formatters';

interface SpanBarProps {
  span: ParsedSpan;
  totalDurationMs: number;
  isSelected: boolean;
  onClick: () => void;
}

export const SpanBar: React.FC<SpanBarProps> = ({
  span,
  totalDurationMs,
  isSelected,
  onClick,
}) => {
  const safeTotal = Math.max(1, totalDurationMs);
  const leftPercent = Math.min(99.5, Math.max(0, (span.startOffsetMs / safeTotal) * 100));
  const rawWidthPercent = (span.durationMs / safeTotal) * 100;
  const widthPercent = Math.min(100 - leftPercent, Math.max(0.8, rawWidthPercent));

  // Is this a retry span? (Multiple attempts)
  const hasRetries = span.attempts && span.attempts.length > 1;

  return (
    <div
      onClick={onClick}
      className={`apple-press relative h-8 w-full rounded-xl flex items-center cursor-pointer transition-all group ${
        isSelected ? 'ring-2 ring-blue-500 bg-blue-50/40' : 'hover:bg-slate-50'
      }`}
    >
      {/* Background track line */}
      <div className="absolute inset-x-0 h-px bg-slate-200" />

      {/* The Span Waterfall Bar */}
      <div
        style={{
          left: `${leftPercent}%`,
          width: `${widthPercent}%`,
        }}
        className={`absolute h-5 rounded-lg flex items-center transition-all shadow-xs ${
          span.status === 'TIMEOUT'
            ? 'bg-rose-500 border border-rose-600/40'
            : span.status === 'REJECTED'
            ? 'bg-amber-500 border border-amber-600/40'
            : span.status === 'APPROVED'
            ? 'bg-emerald-500 border border-emerald-600/40'
            : 'bg-blue-500 border border-blue-600/40'
        }`}
      >
        {/* If retries exist, draw attempt partitions within the bar */}
        {hasRetries ? (
          <div className="flex h-full w-full rounded-lg overflow-hidden">
            {span.attempts.map((att, idx) => {
              const isLast = idx === span.attempts.length - 1;
              return (
                <React.Fragment key={att.attempt}>
                  {/* Attempt duration bar */}
                  <div
                    title={`Attempt ${att.attempt} (${att.status})`}
                    className={`h-full flex items-center justify-center text-[9px] font-mono font-bold text-white px-1 border-r border-black/20 transition-all ${
                      att.status === 'timed_out'
                        ? 'bg-rose-600 hover:bg-rose-700'
                        : att.status === 'success'
                        ? 'bg-emerald-600'
                        : 'bg-rose-700'
                    }`}
                    style={{ flex: att.durationMs || 2000 }}
                  >
                    <span className="truncate">Att #{att.attempt}</span>
                  </div>

                  {/* Backoff delay spacer if not last */}
                  {!isLast && att.backoffMs && (
                    <div
                      title={`Backoff delay: ${att.backoffMs}ms`}
                      className="h-full bg-slate-800/80 border-r border-black/20 flex items-center justify-center text-[8px] font-mono text-slate-300"
                      style={{ flex: att.backoffMs }}
                    >
                      <span className="truncate">+{att.backoffMs}ms</span>
                    </div>
                  )}
                </React.Fragment>
              );
            })}
          </div>
        ) : (
          <div className="px-2 w-full flex items-center justify-between text-[10px] font-mono font-bold text-white">
            <span className="truncate">{formatDurationMs(span.durationMs)}</span>
          </div>
        )}
      </div>

      {/* Latency text tag positioned next to bar */}
      <div
        style={{
          left: `calc(${leftPercent + widthPercent}% + 8px)`,
        }}
        className="absolute hidden sm:flex items-center space-x-1.5 text-[11px] font-mono whitespace-nowrap"
      >
        <span className="font-bold text-[#1d1d1f]">
          {formatDurationMs(span.durationMs)}
        </span>
        {hasRetries && (
          <span className="px-1.5 py-0.5 bg-rose-50 text-rose-700 border border-rose-200 rounded-full text-[9px] font-sans font-bold">
            {span.attempts.length} Attempts
          </span>
        )}
      </div>
    </div>
  );
};
