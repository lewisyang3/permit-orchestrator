import React, { useState, useEffect, useCallback } from 'react';
import { DepartmentName, FaultMode, AdminFaultResponse, FaultControlResponse } from '../types';
import { fetchAdminFaults, setDepartmentFault, restoreAllDepartmentsHealthy } from '../utils/api';
import {
  Flame,
  Home,
  Droplet,
  Shield,
  Zap,
  RotateCcw,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  Clock,
  ShieldAlert,
} from 'lucide-react';

interface FaultControlPanelProps {
  onRunFaultDemo?: () => void;
  isBackendOnline: boolean;
}

const DEPARTMENTS: { name: DepartmentName; label: string; icon: React.ReactNode }[] = [
  { name: 'fire', label: 'Fire Department', icon: <Flame className="h-4 w-4 text-rose-400" /> },
  { name: 'zoning', label: 'Zoning Department', icon: <Home className="h-4 w-4 text-indigo-400" /> },
  { name: 'water', label: 'Water Department', icon: <Droplet className="h-4 w-4 text-sky-400" /> },
  { name: 'structural', label: 'Structural Department', icon: <Shield className="h-4 w-4 text-emerald-400" /> },
];

export const FaultControlPanel: React.FC<FaultControlPanelProps> = ({
  onRunFaultDemo,
  isBackendOnline,
}) => {
  const [faultStatuses, setFaultStatuses] = useState<Partial<AdminFaultResponse>>({});
  const [loading, setLoading] = useState(false);
  const [updatingDept, setUpdatingDept] = useState<DepartmentName | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const loadFaultStatus = useCallback(async () => {
    if (!isBackendOnline) return;
    setLoading(true);
    setErrorMsg(null);
    try {
      const data = await fetchAdminFaults();
      setFaultStatuses(data);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to fetch department fault statuses');
    } finally {
      setLoading(false);
    }
  }, [isBackendOnline]);

  useEffect(() => {
    loadFaultStatus();
  }, [loadFaultStatus]);

  const handleSetFault = async (department: DepartmentName, mode: FaultMode, delay_ms: number = 3000) => {
    setUpdatingDept(department);
    setErrorMsg(null);
    setActionSuccess(null);
    try {
      const resp = await setDepartmentFault({ department, mode, delay_ms });
      setFaultStatuses((prev) => ({
        ...prev,
        [department]: resp,
      }));
      setActionSuccess(`Fault mode for ${department.toUpperCase()} set to ${mode.toUpperCase()}`);
      setTimeout(() => setActionSuccess(null), 4000);
    } catch (err: any) {
      setErrorMsg(`Failed to configure fault for ${department}: ${err.message}`);
    } finally {
      setUpdatingDept(null);
    }
  };

  const handleSimulateFireOutage = async () => {
    setErrorMsg(null);
    setActionSuccess(null);
    try {
      await handleSetFault('fire', 'offline');
      if (onRunFaultDemo) {
        onRunFaultDemo();
      }
    } catch (err: any) {
      setErrorMsg(err.message);
    }
  };

  const handleRestoreAll = async () => {
    setLoading(true);
    setErrorMsg(null);
    setActionSuccess(null);
    try {
      const res = await restoreAllDepartmentsHealthy();
      setFaultStatuses(res);
      setActionSuccess('All 4 departments restored to HEALTHY (mode: none)');
      setTimeout(() => setActionSuccess(null), 4000);
    } catch (err: any) {
      setErrorMsg(`Restore failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const getModeBadge = (status?: FaultControlResponse) => {
    const mode = status?.mode || 'none';
    switch (mode) {
      case 'offline':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-500/20 text-rose-300 border border-rose-500/40 flex items-center space-x-1 animate-pulse">
            <ShieldAlert className="h-3 w-3 mr-0.5" />
            <span>OFFLINE (503)</span>
          </span>
        );
      case 'slow':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40 flex items-center space-x-1">
            <Clock className="h-3 w-3 mr-0.5" />
            <span>SLOW ({status?.delay_ms || 3000}ms)</span>
          </span>
        );
      case 'error':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-red-500/20 text-red-300 border border-red-500/40 flex items-center space-x-1">
            <AlertTriangle className="h-3 w-3 mr-0.5" />
            <span>ERROR (500)</span>
          </span>
        );
      case 'none':
      default:
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center space-x-1">
            <CheckCircle2 className="h-3 w-3 mr-0.5" />
            <span>HEALTHY</span>
          </span>
        );
    }
  };

  return (
    <div className="bg-slate-900/90 rounded-xl border border-slate-800 shadow-xl overflow-hidden">
      {/* Panel Header */}
      <div className="p-4 border-b border-slate-800 bg-slate-950/70 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center space-x-2.5">
          <div className="p-1.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-400">
            <Zap className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-white flex items-center space-x-2">
              <span>Fault Injection &amp; Chaos Controls</span>
              <span className="px-1.5 py-0.2 rounded text-[10px] font-mono bg-slate-800 text-slate-300 border border-slate-700">
                POST /admin/fault
              </span>
            </h3>
            <p className="text-xs text-slate-400">
              Inject failure modes live into department containers to demonstrate timeout, retry, and fail-safe consensus.
            </p>
          </div>
        </div>

        {/* Global Action Buttons */}
        <div className="flex items-center space-x-2">
          {/* Quick 1-minute demo trigger button */}
          <button
            type="button"
            disabled={!isBackendOnline || loading || updatingDept !== null}
            onClick={handleSimulateFireOutage}
            className="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold flex items-center space-x-1.5 transition-colors disabled:opacity-50 shadow-sm"
          >
            <Zap className="h-3.5 w-3.5" />
            <span>Kill Fire Department (Outage Demo)</span>
          </button>

          {/* Restore all button */}
          <button
            type="button"
            disabled={!isBackendOnline || loading || updatingDept !== null}
            onClick={handleRestoreAll}
            className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs font-medium flex items-center space-x-1.5 transition-colors disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>Restore All</span>
          </button>

          {/* Refresh status */}
          <button
            type="button"
            disabled={!isBackendOnline || loading}
            onClick={loadFaultStatus}
            className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white border border-slate-700 transition-colors disabled:opacity-50"
            title="Refresh fault statuses"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin text-sky-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* Notifications */}
      {errorMsg && (
        <div className="px-4 py-2 bg-rose-950/40 border-b border-rose-500/30 text-rose-300 text-xs flex items-center space-x-2">
          <AlertTriangle className="h-4 w-4 text-rose-400 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {actionSuccess && (
        <div className="px-4 py-2 bg-emerald-950/40 border-b border-emerald-500/30 text-emerald-300 text-xs flex items-center space-x-2">
          <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
          <span>{actionSuccess}</span>
        </div>
      )}

      {!isBackendOnline && (
        <div className="px-4 py-2.5 bg-amber-950/30 border-b border-amber-500/30 text-amber-300 text-xs flex items-center space-x-2">
          <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0" />
          <span>
            Backend orchestrator is offline (<code className="font-mono bg-amber-900/40 px-1 py-0.5 rounded">http://localhost:8000</code>). Start with <code className="font-mono bg-amber-900/40 px-1 py-0.5 rounded">docker compose up</code> or <code className="font-mono bg-amber-900/40 px-1 py-0.5 rounded">uv run uvicorn orchestrator.app:app</code> to interact with live containers.
          </span>
        </div>
      )}

      {/* 4 Departments Grid */}
      <div className="p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {DEPARTMENTS.map((dept) => {
          const currentStatus = faultStatuses[dept.name];
          const currentMode = currentStatus?.mode || 'none';
          const isUpdating = updatingDept === dept.name;

          return (
            <div
              key={dept.name}
              className={`p-3.5 rounded-xl border text-xs transition-all flex flex-col justify-between ${
                currentMode !== 'none'
                  ? 'bg-rose-950/20 border-rose-500/40 shadow-sm shadow-rose-950/30'
                  : 'bg-slate-950/60 border-slate-800/80 hover:border-slate-700'
              }`}
            >
              <div>
                {/* Department Header */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center space-x-2">
                    <span className="shrink-0">{dept.icon}</span>
                    <span className="font-bold text-slate-100">{dept.label}</span>
                  </div>
                  {getModeBadge(currentStatus)}
                </div>

                <p className="text-[11px] text-slate-400 mb-3 truncate">
                  {currentStatus?.message || `${dept.label} operating normally`}
                </p>
              </div>

              {/* Mode Control Buttons */}
              <div className="space-y-1.5 pt-2 border-t border-slate-800/80">
                <span className="text-[10px] uppercase font-semibold text-slate-500 tracking-wider block">
                  Fault Mode:
                </span>
                <div className="grid grid-cols-2 gap-1.5 text-[11px]">
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'none')}
                    className={`px-2 py-1 rounded font-medium transition-colors ${
                      currentMode === 'none'
                        ? 'bg-emerald-600 text-white'
                        : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                    }`}
                  >
                    Healthy
                  </button>
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'offline')}
                    className={`px-2 py-1 rounded font-medium transition-colors ${
                      currentMode === 'offline'
                        ? 'bg-rose-600 text-white'
                        : 'bg-slate-800 text-slate-300 hover:bg-rose-950/60 hover:text-rose-300'
                    }`}
                  >
                    Offline (503)
                  </button>
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'slow', 3000)}
                    className={`px-2 py-1 rounded font-medium transition-colors ${
                      currentMode === 'slow'
                        ? 'bg-amber-600 text-white'
                        : 'bg-slate-800 text-slate-300 hover:bg-amber-950/60 hover:text-amber-300'
                    }`}
                  >
                    Slow (3.0s)
                  </button>
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'error')}
                    className={`px-2 py-1 rounded font-medium transition-colors ${
                      currentMode === 'error'
                        ? 'bg-red-600 text-white'
                        : 'bg-slate-800 text-slate-300 hover:bg-red-950/60 hover:text-red-300'
                    }`}
                  >
                    Error (500)
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
