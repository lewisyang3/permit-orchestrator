import React, { useRef, useEffect } from 'react';
import {
  FileCheck2,
  Clock,
  Check,
  Radio,
  Play,
  ArrowRight,
} from 'lucide-react';
import { AppTab } from './Header';

export interface KisoDropdownMenuProps {
  isOpen: boolean;
  onClose: () => void;
  activeTab?: AppTab;
  onTabChange?: (tab: AppTab) => void;
  mode: 'fixture' | 'live';
  onModeChange: (mode: 'fixture' | 'live') => void;
  isBackendOnline: boolean;
  onSelectDemo?: (scenarioId: 'approved' | 'rejected' | 'timeout') => void;
  align?: 'left' | 'right';
  anchorRef?: React.RefObject<HTMLElement | null>;
}

export const KISO_TAB_CONFIG: {
  id: AppTab;
  label: string;
  badge: string;
  icon: React.ReactNode;
  desc: string;
}[] = [
  {
    id: 'review',
    label: 'Permit Review',
    badge: 'Core Engine',
    icon: <FileCheck2 className="h-4 w-4 text-[#1c1d21]" />,
    desc: 'Automated plan intake & multi-department consensus evaluation',
  },
  {
    id: 'trace',
    label: 'Review Timeline',
    badge: 'Observability',
    icon: <Clock className="h-4 w-4 text-[#1c1d21]" />,
    desc: 'Distributed trace execution waterfall with span latency metrics',
  },
];

export const KisoDropdownMenu: React.FC<KisoDropdownMenuProps> = ({
  isOpen,
  onClose,
  activeTab = 'review',
  onTabChange,
  mode,
  onModeChange,
  isBackendOnline,
  onSelectDemo,
  align = 'right',
}) => {
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Close when clicking outside or pressing Escape
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSelectTab = (tabId: AppTab) => {
    if (onTabChange) {
      onTabChange(tabId);
    }
    onClose();

    // Smooth scroll to workbench
    const workbenchEl = document.getElementById('app-workbench');
    if (workbenchEl) {
      workbenchEl.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const handleTriggerDemo = (scenarioId: 'approved' | 'rejected' | 'timeout') => {
    onSelectDemo?.(scenarioId);
    onClose();

    const workbenchEl = document.getElementById('app-workbench');
    if (workbenchEl) {
      workbenchEl.scrollIntoView({ behavior: 'smooth' });
    }
  };

  return (
    <div
      ref={menuRef}
      className={`absolute ${
        align === 'left' ? 'left-0' : 'right-0'
      } mt-2 w-80 sm:w-96 rounded-2xl bg-white/95 backdrop-blur-2xl border border-[#1c1d21]/15 shadow-2xl p-2 z-50 animate-in fade-in zoom-in-95 duration-150 text-left`}
      role="menu"
      style={{
        backgroundImage: 'radial-gradient(rgba(0,0,0,0.02) 1px, transparent 0)',
        backgroundSize: '12px 12px',
      }}
    >
      {/* Header bar with drafted style */}
      <div className="px-3 py-2 border-b border-[#1c1d21]/10 flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <span className="w-1.5 h-1.5 rounded-full bg-[#1c1d21]" />
          <span className="text-[11px] font-bold text-[#1c1d21] tracking-wider uppercase font-mono">
            KISO Workspace &amp; Review
          </span>
        </div>
        <div className="flex items-center space-x-1.5 text-[10px] font-mono">
          <span
            className={`w-2 h-2 rounded-full ${
              isBackendOnline ? 'bg-emerald-500 animate-pulse' : 'bg-amber-400'
            }`}
          />
          <span className="text-slate-600 font-semibold">
            {isBackendOnline ? 'API :8000 Online' : 'Fixture Mode'}
          </span>
        </div>
      </div>

      {/* Primary Navigation Sections */}
      <div className="p-1.5 space-y-1">
        <div className="px-2 pt-1.5 pb-1 text-[10px] font-bold text-slate-400 tracking-wider uppercase font-mono">
          Orchestrator Modules
        </div>
        {KISO_TAB_CONFIG.map((tab) => {
          const isSelected = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="menuitem"
              onClick={() => handleSelectTab(tab.id)}
              className={`w-full flex items-start justify-between p-2.5 rounded-xl text-xs transition-all text-left cursor-pointer group ${
                isSelected
                  ? 'bg-[#1c1d21] text-white shadow-sm'
                  : 'text-slate-800 hover:bg-slate-100/90 hover:text-[#1c1d21]'
              }`}
            >
              <div className="flex items-start space-x-3 truncate">
                <div
                  className={`p-2 rounded-lg shrink-0 mt-0.5 transition-colors ${
                    isSelected
                      ? 'bg-white/15 text-white'
                      : 'bg-slate-100 text-slate-700 group-hover:bg-slate-200'
                  }`}
                >
                  {tab.icon}
                </div>
                <div className="truncate">
                  <div className="flex items-center space-x-2">
                    <span className="font-bold text-xs">{tab.label}</span>
                    <span
                      className={`text-[9px] font-mono uppercase px-1.5 py-0.2 rounded ${
                        isSelected
                          ? 'bg-white/20 text-white'
                          : 'bg-slate-200/70 text-slate-600'
                      }`}
                    >
                      {tab.badge}
                    </span>
                  </div>
                  <p
                    className={`text-[11px] mt-0.5 leading-snug line-clamp-1 ${
                      isSelected ? 'text-slate-300' : 'text-slate-500'
                    }`}
                  >
                    {tab.desc}
                  </p>
                </div>
              </div>
              {isSelected ? (
                <Check className="h-4 w-4 text-white shrink-0 ml-2 mt-1" />
              ) : (
                <ArrowRight className="h-3.5 w-3.5 text-slate-400 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 ml-2 mt-1" />
              )}
            </button>
          );
        })}
      </div>

      {/* Quick Demo Previews */}
      <div className="mt-1 pt-2 border-t border-[#1c1d21]/10 px-2 pb-1.5">
        <div className="px-1 mb-1.5 flex items-center justify-between">
          <span className="text-[10px] font-bold text-slate-400 tracking-wider uppercase font-mono">
            Instant Demos
          </span>
          <span className="text-[10px] text-slate-500 font-mono">1-click test</span>
        </div>
        <div className="grid grid-cols-3 gap-1.5">
          <button
            type="button"
            onClick={() => handleTriggerDemo('approved')}
            className="apple-press p-2 rounded-xl bg-emerald-50/70 hover:bg-emerald-100/80 border border-emerald-200/80 text-emerald-950 text-left transition-all text-[11px]"
          >
            <div className="flex items-center space-x-1 font-bold text-emerald-900">
              <Play className="h-3 w-3 fill-emerald-600 text-emerald-600" />
              <span>Approved</span>
            </div>
            <p className="text-[10px] text-emerald-700 truncate mt-0.5">Addition Plan</p>
          </button>

          <button
            type="button"
            onClick={() => handleTriggerDemo('rejected')}
            className="apple-press p-2 rounded-xl bg-amber-50/70 hover:bg-amber-100/80 border border-amber-200/80 text-amber-950 text-left transition-all text-[11px]"
          >
            <div className="flex items-center space-x-1 font-bold text-amber-900">
              <Play className="h-3 w-3 fill-amber-600 text-amber-600" />
              <span>Rejection</span>
            </div>
            <p className="text-[10px] text-amber-700 truncate mt-0.5">Fire Code Viol.</p>
          </button>

          <button
            type="button"
            onClick={() => handleTriggerDemo('timeout')}
            className="apple-press p-2 rounded-xl bg-rose-50/70 hover:bg-rose-100/80 border border-rose-200/80 text-rose-950 text-left transition-all text-[11px]"
          >
            <div className="flex items-center space-x-1 font-bold text-rose-900">
              <Play className="h-3 w-3 fill-rose-600 text-rose-600" />
              <span>Fail-Safe</span>
            </div>
            <p className="text-[10px] text-rose-700 truncate mt-0.5">Timeout Retry</p>
          </button>
        </div>
      </div>

      {/* Backend Mode Switcher Footer */}
      <div className="mt-1 pt-2 border-t border-[#1c1d21]/10 px-2 py-1.5 flex items-center justify-between text-xs bg-slate-50/80 rounded-b-xl">
        <span className="text-[11px] font-semibold text-slate-600">Execution Mode:</span>
        <div className="flex items-center space-x-1 bg-white p-0.5 rounded-lg border border-slate-200 shadow-2xs">
          <button
            type="button"
            onClick={() => {
              onModeChange('fixture');
            }}
            className={`px-2 py-1 rounded-md text-[11px] font-bold transition-all ${
              mode === 'fixture'
                ? 'bg-[#1c1d21] text-white'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Fixtures
          </button>
          <button
            type="button"
            onClick={() => {
              onModeChange('live');
            }}
            className={`px-2 py-1 rounded-md text-[11px] font-bold flex items-center space-x-1 transition-all ${
              mode === 'live'
                ? 'bg-[#1c1d21] text-white'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Radio className="h-2.5 w-2.5" />
            <span>Live SSE</span>
          </button>
        </div>
      </div>
    </div>
  );
};
