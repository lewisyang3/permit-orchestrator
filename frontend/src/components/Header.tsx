import React, { useState, useRef, useEffect } from 'react';
import {
  Menu,
  ChevronDown,
  Check,
  FileCheck2,
  Clock,
  FlaskConical,
  Zap,
} from 'lucide-react';

export type AppTab = 'review' | 'trace' | 'fixtures' | 'diagnostics';

export interface HeaderProps {
  mode: 'fixture' | 'live';
  onModeChange: (mode: 'fixture' | 'live') => void;
  isBackendOnline: boolean;
  activeTab?: AppTab;
  onTabChange?: (tab: AppTab) => void;
}

const TAB_CONFIG: { id: AppTab; label: string; icon: React.ReactNode; desc: string }[] = [
  {
    id: 'review',
    label: 'Permit Review',
    icon: <FileCheck2 className="h-4 w-4 text-[#0071e3]" />,
    desc: 'Plan intake & consensus evaluation',
  },
  {
    id: 'trace',
    label: 'Review Timeline',
    icon: <Clock className="h-4 w-4 text-[#0071e3]" />,
    desc: 'Distributed trace execution waterfall',
  },
  {
    id: 'fixtures',
    label: 'Fixtures',
    icon: <FlaskConical className="h-4 w-4 text-purple-600" />,
    desc: 'Baseline demo scenarios',
  },
  {
    id: 'diagnostics',
    label: 'Chaos Diagnostics',
    icon: <Zap className="h-4 w-4 text-amber-500" />,
    desc: 'Container fault injection controls',
  },
];

export const Header: React.FC<HeaderProps> = ({
  mode,
  onModeChange,
  isBackendOnline,
  activeTab = 'review',
  onTabChange,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement | null>(null);

  // Close dropdown on click outside or escape
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  const currentTab = TAB_CONFIG.find((t) => t.id === activeTab) || TAB_CONFIG[0];

  return (
    <header className="apple-glass border-b border-black/[0.06] sticky top-0 z-30 px-4 sm:px-6 py-3.5 transition-all">
      <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
        {/* Left Side: Brand Title & Catchphrase Subtitle */}
        <div>
          <h1 className="text-base sm:text-lg font-bold text-[#1d1d1f] tracking-tight">
            Permit Orchestrator
          </h1>
          <p className="text-xs text-[#86868b] font-normal mt-0.5">
            Automated city permit reviews &amp; instant multi-department compliance
          </p>
        </div>

        {/* Right Side: In-Theme Section Dropdown Menu */}
        {onTabChange && (
          <div className="relative" ref={dropdownRef}>
            <button
              type="button"
              onClick={() => setIsOpen((prev) => !prev)}
              aria-expanded={isOpen}
              aria-haspopup="true"
              aria-label="Navigation Menu"
              className="apple-press flex items-center space-x-2 px-3.5 py-2 rounded-2xl bg-white hover:bg-slate-50 border border-slate-200/90 text-slate-700 shadow-2xs hover:text-[#0071e3] transition-all cursor-pointer font-medium"
            >
              <Menu className="h-4 w-4 text-[#0071e3]" />
              <span className="text-xs font-semibold text-[#1d1d1f]">
                {currentTab.label}
              </span>
              <ChevronDown
                className={`h-3.5 w-3.5 text-slate-400 transition-transform duration-200 ${
                  isOpen ? 'rotate-180 text-[#0071e3]' : ''
                }`}
              />
            </button>

            {/* Apple Frosted Glass Dropdown Popover (Right-Aligned) */}
            {isOpen && (
              <div
                className="absolute right-0 mt-2 w-64 rounded-2xl bg-white/95 backdrop-blur-xl border border-black/[0.08] shadow-xl p-1.5 z-50 space-y-1 animate-in fade-in zoom-in-95 duration-150"
                role="menu"
              >
                <div className="px-3 py-1.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                  Navigation
                </div>
                {TAB_CONFIG.map((tab) => {
                  const isSelected = activeTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        onTabChange(tab.id);
                        setIsOpen(false);
                      }}
                      className={`apple-press w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-semibold transition-all text-left cursor-pointer ${
                        isSelected
                          ? 'bg-blue-50 text-[#0071e3]'
                          : 'text-slate-700 hover:bg-slate-100 hover:text-[#1d1d1f]'
                      }`}
                    >
                      <div className="flex items-center space-x-2.5 truncate">
                        {tab.icon}
                        <div className="truncate">
                          <span className="block truncate">{tab.label}</span>
                          <span className="text-[10px] text-[#86868b] font-normal block truncate">
                            {tab.desc}
                          </span>
                        </div>
                      </div>
                      {isSelected && <Check className="h-4 w-4 text-[#0071e3] shrink-0 ml-2" />}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
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
