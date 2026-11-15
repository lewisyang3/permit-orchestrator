import React from 'react';
import { ShieldCheck, Activity, Layers, Cpu, Wifi, WifiOff, FileCheck2, Clock, Zap } from 'lucide-react';

export type AppTab = 'review' | 'trace' | 'diagnostics';

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
    <header className="apple-glass border-b border-black/[0.08] sticky top-0 z-30 px-4 sm:px-6 py-2.5 transition-colors">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-3 sm:gap-4">
        {/* Brand & Subtitle */}
        <div className="flex items-center space-x-3 w-full md:w-auto justify-between md:justify-start">
          <div className="flex items-center space-x-3">
            <div className="h-9 w-9 rounded-2xl bg-gradient-to-tr from-[#0071e3] to-[#42a5f5] flex items-center justify-center text-white shadow-xs">
              <Cpu className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h1 className="text-base font-bold text-[#1d1d1f] tracking-tight">Permit Orchestrator</h1>
                <span className="px-2 py-0.5 text-[10px] font-semibold bg-blue-50 text-[#0071e3] border border-blue-200/60 rounded-full">
                  v1.0
                </span>
              </div>
              <p className="text-xs text-[#86868b] hidden sm:block">
                Distributed Saga Consensus &amp; Jaeger-Style Trace Waterfall
              </p>
            </div>
          </div>

          {/* Mobile Backend Status indicator */}
          <div className="sm:hidden flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-xs font-medium border bg-white border-slate-200 shadow-2xs">
            <span
              className={`h-2 w-2 rounded-full ${
                isBackendOnline ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'
              }`}
            />
            <span className="text-[11px] text-slate-700">
              {isBackendOnline ? 'Online' : 'Offline'}
            </span>
          </div>
        </div>

        {/* Center: Apple Segmented Tab Control */}
        {onTabChange && (
          <nav
            aria-label="Application Sections"
            className="bg-[#e5e5ea]/80 p-1 rounded-2xl flex items-center shadow-inner max-w-full overflow-x-auto"
          >
            <button
              type="button"
              onClick={() => onTabChange('review')}
              className={`apple-press flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all whitespace-nowrap ${
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
              className={`apple-press flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all whitespace-nowrap ${
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
              onClick={() => onTabChange('diagnostics')}
              className={`apple-press flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all whitespace-nowrap ${
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

        {/* Right: Environment & Mode Switches */}
        <div className="flex items-center space-x-2.5 text-xs w-full md:w-auto justify-end">
          {/* Backend Connectivity Status */}
          <div
            className={`hidden sm:flex items-center space-x-1.5 px-3 py-1.5 rounded-full border text-xs font-medium transition-colors ${
              isBackendOnline
                ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
                : 'bg-amber-50 border-amber-200 text-amber-800'
            }`}
          >
            {isBackendOnline ? (
              <>
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
                <Wifi className="h-3.5 w-3.5 text-emerald-600" />
                <span>Backend :8000 Online</span>
              </>
            ) : (
              <>
                <WifiOff className="h-3.5 w-3.5 text-amber-600" />
                <span>Backend Offline</span>
              </>
            )}
          </div>

          {/* Mode Switcher Segmented Pill */}
          <div className="bg-[#e5e5ea]/80 p-0.5 rounded-2xl flex items-center shadow-inner">
            <button
              type="button"
              onClick={() => onModeChange('fixture')}
              className={`apple-press px-3 py-1.5 rounded-xl font-semibold transition-all ${
                mode === 'fixture'
                  ? 'bg-white text-[#1d1d1f] shadow-xs'
                  : 'text-[#6e6e73] hover:text-[#1d1d1f]'
              }`}
            >
              <span className="flex items-center space-x-1.5">
                <Layers className="h-3.5 w-3.5 text-emerald-600" />
                <span>Golden Fixtures</span>
              </span>
            </button>
            <button
              type="button"
              onClick={() => onModeChange('live')}
              className={`apple-press px-3 py-1.5 rounded-xl font-semibold transition-all ${
                mode === 'live'
                  ? 'bg-[#0071e3] text-white shadow-xs'
                  : 'text-[#6e6e73] hover:text-[#1d1d1f]'
              }`}
            >
              <span className="flex items-center space-x-1.5">
                <Activity className="h-3.5 w-3.5" />
                <span>Live Orchestrator (SSE)</span>
              </span>
            </button>
          </div>

          {/* Guarantee Pill */}
          <div className="hidden xl:flex items-center space-x-1.5 px-3 py-1.5 bg-white rounded-full border border-black/[0.08] text-slate-700 shadow-2xs">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
            <span className="text-[11px] font-medium">Consensus Fail-Safe Enforced</span>
          </div>
        </div>
      </div>
    </header>
  );
};
