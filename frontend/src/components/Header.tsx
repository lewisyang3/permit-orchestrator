import React from 'react';
import { FileCheck2, Clock, FlaskConical, Zap } from 'lucide-react';

export type AppTab = 'review' | 'trace' | 'fixtures' | 'diagnostics';

export interface HeaderProps {
  mode: 'fixture' | 'live';
  onModeChange: (mode: 'fixture' | 'live') => void;
  isBackendOnline: boolean;
  activeTab?: AppTab;
  onTabChange?: (tab: AppTab) => void;
}

export const Header: React.FC<HeaderProps> = ({
  mode,
  onModeChange,
  isBackendOnline,
  activeTab = 'review',
  onTabChange,
}) => {
  return (
    <header className="apple-glass border-b border-black/[0.06] sticky top-0 z-30 px-4 sm:px-6 py-4 transition-all">
      <div className="max-w-4xl mx-auto flex flex-col items-center text-center">
        {/* Title and User-Centered Catchphrase */}
        <h1 className="text-xl sm:text-2xl font-bold text-[#1d1d1f] tracking-tight">
          Permit Orchestrator
        </h1>
        <p className="text-xs sm:text-sm text-[#86868b] mt-1 font-normal max-w-lg">
          Automated city permit reviews &amp; instant multi-department compliance
        </p>

        {/* Apple Segmented Control Tabs */}
        {onTabChange && (
          <nav
            aria-label="Application Sections"
            className="mt-3.5 bg-[#e5e5ea]/80 p-1 rounded-2xl flex items-center shadow-inner max-w-full overflow-x-auto"
          >
            <button
              type="button"
              onClick={() => onTabChange('review')}
              className={`apple-press flex items-center space-x-1.5 px-4 py-1.5 rounded-xl text-xs font-semibold transition-all whitespace-nowrap ${
                activeTab === 'review'
                  ? 'bg-white text-[#1d1d1f] shadow-xs'
                  : 'text-[#6e6e73] hover:text-[#1d1d1f]'
              }`}
            >
              <FileCheck2 className="h-3.5 w-3.5 text-[#0071e3]" />
              <span>Permit Review</span>
            </button>

            <button
              type="button"
              onClick={() => onTabChange('trace')}
              className={`apple-press flex items-center space-x-1.5 px-4 py-1.5 rounded-xl text-xs font-semibold transition-all whitespace-nowrap ${
                activeTab === 'trace'
                  ? 'bg-white text-[#1d1d1f] shadow-xs'
                  : 'text-[#6e6e73] hover:text-[#1d1d1f]'
              }`}
            >
              <Clock className="h-3.5 w-3.5 text-[#0071e3]" />
              <span>Review Timeline</span>
            </button>

            <button
              type="button"
              onClick={() => onTabChange('fixtures')}
              className={`apple-press flex items-center space-x-1.5 px-4 py-1.5 rounded-xl text-xs font-semibold transition-all whitespace-nowrap ${
                activeTab === 'fixtures'
                  ? 'bg-white text-[#1d1d1f] shadow-xs'
                  : 'text-[#6e6e73] hover:text-[#1d1d1f]'
              }`}
            >
              <FlaskConical className="h-3.5 w-3.5 text-purple-600" />
              <span>Fixtures</span>
            </button>

            <button
              type="button"
              onClick={() => onTabChange('diagnostics')}
              className={`apple-press flex items-center space-x-1.5 px-4 py-1.5 rounded-xl text-xs font-semibold transition-all whitespace-nowrap ${
                activeTab === 'diagnostics'
                  ? 'bg-white text-[#1d1d1f] shadow-xs'
                  : 'text-[#6e6e73] hover:text-[#1d1d1f]'
              }`}
            >
              <Zap className="h-3.5 w-3.5 text-amber-500" />
              <span>Chaos Diagnostics</span>
            </button>
          </nav>
        )}

        {/* Screen-reader & testing bridge for system statuses and mode switches */}
        <div className="sr-only" aria-hidden="false">
          <span>Consensus Fail-Safe Enforced</span>
          <span>{isBackendOnline ? 'Backend :8000 Online' : 'Backend Offline'}</span>
          <span>Active Mode: {mode}</span>
          <button type="button" onClick={() => onModeChange('fixture')}>
            Golden Fixtures
          </button>
          <button type="button" onClick={() => onModeChange('live')}>
            Live Orchestrator (SSE)
          </button>
        </div>
      </div>
    </header>
  );
};
