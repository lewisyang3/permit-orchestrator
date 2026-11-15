import React from 'react';
import { ShieldCheck, Activity, Layers, Cpu, Wifi, WifiOff } from 'lucide-react';

interface HeaderProps {
  mode: 'fixture' | 'live';
  onModeChange: (mode: 'fixture' | 'live') => void;
  isBackendOnline: boolean;
}

export const Header: React.FC<HeaderProps> = ({ mode, onModeChange, isBackendOnline }) => {
  return (
    <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-30 px-6 py-3.5">
      <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
        {/* Brand */}
        <div className="flex items-center space-x-3">
          <div className="h-9 w-9 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
            <Cpu className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-base font-bold text-white tracking-tight">Permit Orchestrator</h1>
              <span className="px-1.5 py-0.5 text-[10px] font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 rounded">
                v1.0
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Distributed Saga Consensus &amp; Jaeger-Style Trace Waterfall
            </p>
          </div>
        </div>

        {/* Mode Toggle & Status Pills */}
        <div className="flex items-center space-x-3 text-xs">
          {/* Backend Connectivity Status */}
          <div
            className={`hidden sm:flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-mono ${
              isBackendOnline
                ? 'bg-emerald-950/40 border-emerald-500/40 text-emerald-300'
                : 'bg-amber-950/40 border-amber-500/40 text-amber-300'
            }`}
          >
            {isBackendOnline ? (
              <>
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
                <Wifi className="h-3.5 w-3.5 text-emerald-400" />
                <span>Backend :8000 Online</span>
              </>
            ) : (
              <>
                <WifiOff className="h-3.5 w-3.5 text-amber-400" />
                <span>Backend Offline</span>
              </>
            )}
          </div>

          {/* Mode Switcher */}
          <div className="bg-slate-800/80 p-0.5 rounded-lg border border-slate-700 flex items-center">
            <button
              onClick={() => onModeChange('fixture')}
              className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
                mode === 'fixture'
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <span className="flex items-center space-x-1.5">
                <Layers className="h-3.5 w-3.5" />
                <span>Golden Fixtures</span>
              </span>
            </button>
            <button
              onClick={() => onModeChange('live')}
              className={`px-3 py-1.5 rounded-md font-medium transition-colors ${
                mode === 'live'
                  ? 'bg-sky-600 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <span className="flex items-center space-x-1.5">
                <Activity className="h-3.5 w-3.5" />
                <span>Live Orchestrator (SSE)</span>
              </span>
            </button>
          </div>

          {/* Guarantee Pill */}
          <div className="hidden lg:flex items-center space-x-1.5 px-2.5 py-1.5 bg-slate-800/50 rounded-md border border-slate-700/60 text-slate-300">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
            <span className="text-[11px]">Consensus Fail-Safe Enforced</span>
          </div>
        </div>
      </div>
    </header>
  );
};
