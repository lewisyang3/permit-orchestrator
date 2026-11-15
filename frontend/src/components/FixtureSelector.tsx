import React from 'react';
import { FIXTURE_SCENARIOS, FixtureScenario } from '../fixtures';
import { CheckCircle2, XCircle, AlertTriangle, Play, Zap, FileText } from 'lucide-react';

interface FixtureSelectorProps {
  selectedId: 'approved' | 'rejected' | 'timeout';
  onSelect: (scenario: FixtureScenario) => void;
  onSimulateReplay: (scenario: FixtureScenario) => void;
  isSimulating: boolean;
  onViewPermitJson: (scenario: FixtureScenario) => void;
}

export const FixtureSelector: React.FC<FixtureSelectorProps> = ({
  selectedId,
  onSelect,
  onSimulateReplay,
  isSimulating,
  onViewPermitJson,
}) => {
  const scenarios = Object.values(FIXTURE_SCENARIOS);

  return (
    <div className="space-y-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-bold text-[#1d1d1f] tracking-tight">Select Test Fixture Outcome</h2>
          <p className="text-xs text-[#86868b] font-normal">
            Render contract scenarios with zero backend required (Task T7 acceptance criteria)
          </p>
        </div>
        <div className="flex items-center space-x-2 text-xs">
          <span className="text-[#86868b]">Total Golden Fixtures:</span>
          <span className="px-2.5 py-0.5 rounded-full bg-white text-slate-800 border border-slate-200 font-mono font-semibold shadow-2xs">
            3 / 3 Verified
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5">
        {scenarios.map((sc) => {
          const isSelected = selectedId === sc.id;
          const isApproved = sc.expectedDecision === 'APPROVED';
          const isTimeout = sc.expectedCode === 'INCOMPLETE_REVIEWS';

          return (
            <div
              key={sc.id}
              onClick={() => onSelect(sc)}
              className={`apple-press cursor-pointer rounded-3xl p-5 border transition-all text-left flex flex-col justify-between ${
                isSelected
                  ? 'bg-white border-2 border-[#0071e3] shadow-md shadow-blue-500/10'
                  : 'bg-white border-black/[0.06] hover:border-slate-300 shadow-xs'
              }`}
            >
              <div>
                {/* Header badge */}
                <div className="flex items-center justify-between mb-3">
                  <span
                    className={`inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold tracking-wide uppercase ${
                      isApproved
                        ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
                        : isTimeout
                        ? 'bg-rose-50 text-rose-800 border border-rose-200'
                        : 'bg-amber-50 text-amber-800 border border-amber-200'
                    }`}
                  >
                    {isApproved ? (
                      <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                    ) : isTimeout ? (
                      <AlertTriangle className="h-3.5 w-3.5 mr-1" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5 mr-1" />
                    )}
                    {sc.expectedDecision}: {sc.expectedCode}
                  </span>

                  <span className="text-[11px] text-slate-500 font-mono">
                    {sc.events.length} events
                  </span>
                </div>

                {/* Title */}
                <h3 className="text-sm font-bold text-[#1d1d1f] mb-1">{sc.title}</h3>
                <p className="text-xs text-slate-700 font-semibold mb-1.5">{sc.subtitle}</p>
                <p className="text-xs text-[#86868b] line-clamp-2 leading-relaxed mb-4 font-normal">
                  {sc.description}
                </p>
              </div>

              {/* Action buttons */}
              <div className="pt-3 border-t border-slate-100 flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onViewPermitJson(sc);
                  }}
                  className="apple-press px-2.5 py-1 text-[11px] font-medium text-slate-600 hover:text-slate-900 bg-slate-100 hover:bg-slate-200 rounded-xl border border-slate-200 flex items-center space-x-1"
                >
                  <FileText className="h-3 w-3" />
                  <span>Payload</span>
                </button>

                <div className="flex items-center space-x-1.5">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(sc);
                    }}
                    className={`apple-press px-2.5 py-1 text-[11px] font-semibold rounded-xl flex items-center space-x-1 transition-all ${
                      isSelected
                        ? 'bg-blue-50 text-[#0071e3] border border-blue-200'
                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200 border border-slate-200'
                    }`}
                  >
                    <Zap className="h-3 w-3" />
                    <span>Instant</span>
                  </button>

                  <button
                    type="button"
                    disabled={isSimulating}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSimulateReplay(sc);
                    }}
                    className="apple-press px-2.5 py-1 text-[11px] font-semibold rounded-xl bg-[#0071e3] hover:bg-[#0077ed] text-white flex items-center space-x-1 disabled:opacity-50 shadow-2xs"
                  >
                    <Play className="h-3 w-3" />
                    <span>Replay</span>
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
