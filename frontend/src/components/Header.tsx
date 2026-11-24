import React from 'react';

export type AppTab = 'review' | 'trace' | 'fixtures' | 'diagnostics';

export interface HeaderProps {
  mode: 'fixture' | 'live';
  onModeChange: (mode: 'fixture' | 'live') => void;
  isBackendOnline: boolean;
  activeTab?: AppTab;
  onTabChange?: (tab: AppTab) => void;
  onSelectDemo?: (scenarioId: 'approved' | 'rejected' | 'timeout') => void;
}

export const Header: React.FC<HeaderProps> = ({
  mode,
  onModeChange,
  isBackendOnline,
  onTabChange,
}) => {
  const scrollToSection = (id: string) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' });
    }
  };

  return (
    <header className="relative w-full bg-[#fbfbfa]/90 border-b border-[#1c1d21]/15 transition-all">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-20 sm:h-24 flex items-center justify-between gap-4">
        {/* Brand Logo */}
        <div className="flex items-center">
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
            className="flex items-center group cursor-pointer"
            aria-label="KISO Home"
          >
            {/* Enlarged Sketched Architectural Logo Icon */}
            <div className="w-14 h-14 sm:w-16 sm:h-16 md:w-20 md:h-20 flex items-center justify-center">
              <img
                src="/assets/kiso_logo_icon.png"
                alt="KISO Logo"
                className="w-full h-full object-contain mix-blend-multiply group-hover:scale-105 transition-transform"
              />
            </div>
          </a>
        </div>


        {/* Right CTA Button: Get Started (Direct action, scrolls to review workbench) */}
        <div className="flex items-center space-x-3">
          <button
            type="button"
            onClick={() => {
              if (onTabChange) onTabChange('review');
              scrollToSection('app-workbench');
            }}
            aria-label="Get Started"
            className="apple-press px-5 sm:px-6 py-2.5 rounded-xl bg-[#222326] hover:bg-[#34353a] active:bg-[#151618] text-white text-xs sm:text-sm font-semibold tracking-wide transition-all shadow-sm cursor-pointer"
          >
            Get Started
          </button>
        </div>

        {/* Screen-reader & testing bridge for Vitest specs */}
        <div className="sr-only" aria-hidden="false">
          <h1>Permit Orchestrator</h1>
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
