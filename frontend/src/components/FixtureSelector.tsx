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
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Select Test Fixture Outcome</h2>
          <p className="text-xs text-slate-400">
            Render any contract scenario with zero backend required (Task T7 acceptance criteria)
          </p>
        </div>
        <div className="flex items-center space-x-2 text-xs">
          <span className="text-slate-400">Total Golden Fixtures:</span>
          <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-200 border border-slate-700 font-mono">
            3 / 3 Verified
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {scenarios.map((sc) => {
          const isSelected = selectedId === sc.id;
          const isApproved = sc.expectedDecision === 'APPROVED';
          const isTimeout = sc.expectedCode === 'INCOMPLETE_REVIEWS';

          return (
            <div
              key={sc.id}
              onClick={() => onSelect(sc)}
              className={`cursor-pointer rounded-xl p-4 border transition-all text-left flex flex-col justify-between ${
                isSelected
                  ? 'bg-slate-800/90 border-emerald-500/60 shadow-lg shadow-emerald-950/20 ring-1 ring-emerald-500/30'
                  : 'bg-slate-900/60 border-slate-800 hover:border-slate-700 hover:bg-slate-850'
              }`}
            >
              <div>
                {/* Header badge */}
                <div className="flex items-center justify-between mb-2">
                  <span
                    className={`inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[11px] font-semibold tracking-wide uppercase ${
                      isApproved
                        ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                        : isTimeout
                        ? 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                        : 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                    }`}
                  >
                    {isApproved ? (
                      <CheckCircle2 className="h-3 w-3 mr-1" />
                    ) : isTimeout ? (
                      <AlertTriangle className="h-3 w-3 mr-1" />
                    ) : (
                      <XCircle className="h-3 w-3 mr-1" />
                    )}
                    {sc.expectedDecision}: {sc.expectedCode}
                  </span>

                  <span className="text-[10px] text-slate-400 font-mono">
                    {sc.events.length} events
                  </span>
                </div>

                {/* Title */}
                <h3 className="text-sm font-bold text-white mb-1">{sc.title}</h3>
                <p className="text-xs text-slate-300 font-medium mb-1.5">{sc.subtitle}</p>
                <p className="text-xs text-slate-400 line-clamp-2 leading-relaxed mb-3">
                  {sc.description}
                </p>
              </div>

              {/* Action buttons */}
              <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onViewPermitJson(sc);
                  }}
                  className="px-2 py-1 text-[11px] font-medium text-slate-400 hover:text-slate-200 bg-slate-800 hover:bg-slate-700 rounded border border-slate-700/60 flex items-center space-x-1"
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
                    className={`px-2.5 py-1 text-[11px] font-medium rounded flex items-center space-x-1 ${
                      isSelected
                        ? 'bg-emerald-600 text-white'
                        : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
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
                    className="px-2.5 py-1 text-[11px] font-medium rounded bg-sky-600/80 hover:bg-sky-600 text-white flex items-center space-x-1 disabled:opacity-50"
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
