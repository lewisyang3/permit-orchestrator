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
  ChevronDown,
  ChevronUp,
} from 'lucide-react';

interface FaultControlPanelProps {
  onRunFaultDemo?: () => void;
  isBackendOnline: boolean;
}

const DEPARTMENTS: { name: DepartmentName; label: string; icon: React.ReactNode }[] = [
  { name: 'fire', label: 'Fire Department', icon: <Flame className="h-4 w-4 text-rose-600" /> },
  { name: 'zoning', label: 'Zoning Department', icon: <Home className="h-4 w-4 text-indigo-600" /> },
  { name: 'water', label: 'Water Department', icon: <Droplet className="h-4 w-4 text-sky-600" /> },
  { name: 'structural', label: 'Structural Department', icon: <Shield className="h-4 w-4 text-emerald-600" /> },
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
  const [isExpanded, setIsExpanded] = useState(true);

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
          <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-rose-100 text-rose-800 border border-rose-200 flex items-center space-x-1">
            <ShieldAlert className="h-3 w-3 mr-0.5 text-rose-600" />
            <span>OFFLINE (503)</span>
          </span>
        );
      case 'slow':
        return (
          <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-amber-100 text-amber-800 border border-amber-200 flex items-center space-x-1">
            <Clock className="h-3 w-3 mr-0.5 text-amber-600" />
            <span>SLOW ({status?.delay_ms || 3000}ms)</span>
          </span>
        );
      case 'error':
        return (
          <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-red-100 text-red-800 border border-red-200 flex items-center space-x-1">
            <AlertTriangle className="h-3 w-3 mr-0.5 text-red-600" />
            <span>ERROR (500)</span>
          </span>
        );
      case 'none':
      default:
        return (
          <span className="px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-50 text-emerald-800 border border-emerald-200 flex items-center space-x-1">
            <CheckCircle2 className="h-3 w-3 mr-0.5 text-emerald-600" />
            <span>HEALTHY</span>
          </span>
        );
    }
  };

  return (
    <div className="bg-white rounded-3xl border border-black/[0.06] shadow-xs overflow-hidden transition-all duration-300">
      {/* Panel Header */}
      <div className="p-4 sm:p-5 border-b border-slate-200 bg-slate-50/80 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-2xl bg-amber-50 border border-amber-200/80 text-amber-600">
            <Zap className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="text-sm font-bold text-[#1d1d1f] tracking-tight">Fault Injection &amp; Chaos Controls</h3>
              <span className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-white text-slate-600 border border-slate-200 shadow-xs">
                POST /admin/fault
              </span>
            </div>
            <p className="text-xs text-[#86868b] font-normal">
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
            className="apple-press px-3.5 py-1.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold flex items-center space-x-1.5 shadow-sm shadow-rose-500/25 disabled:opacity-50"
          >
            <Zap className="h-3.5 w-3.5" />
            <span>Kill Fire Department (Outage Demo)</span>
          </button>

          {/* Restore all button */}
          <button
            type="button"
            disabled={!isBackendOnline || loading || updatingDept !== null}
            onClick={handleRestoreAll}
            className="apple-press px-3.5 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-800 border border-slate-200 text-xs font-semibold flex items-center space-x-1.5 disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            <span>Restore All</span>
          </button>

          {/* Refresh status */}
          <button
            type="button"
            disabled={!isBackendOnline || loading}
            onClick={loadFaultStatus}
            className="apple-press p-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-600 hover:text-slate-900 border border-slate-200 disabled:opacity-50"
            title="Refresh fault statuses"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin text-blue-600' : ''}`} />
          </button>

          {/* Disclosure button */}
          <button
            type="button"
            onClick={() => setIsExpanded(!isExpanded)}
            className="apple-press p-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-600 hover:text-slate-900 border border-slate-200"
            title={isExpanded ? 'Collapse department controls' : 'Expand department controls'}
          >
            {isExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>

      {/* Notifications */}
      {errorMsg && (
        <div className="px-4 py-3 bg-rose-50 border-b border-rose-200 text-rose-900 text-xs flex items-center space-x-2">
          <AlertTriangle className="h-4 w-4 text-rose-600 shrink-0" />
          <span className="font-medium">{errorMsg}</span>
        </div>
      )}

      {actionSuccess && (
        <div className="px-4 py-3 bg-emerald-50 border-b border-emerald-200 text-emerald-900 text-xs flex items-center space-x-2">
          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
          <span className="font-medium">{actionSuccess}</span>
        </div>
      )}

      {!isBackendOnline && (
        <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 text-amber-900 text-xs flex items-center space-x-2">
          <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
          <span>
            Backend orchestrator is offline (<code className="font-mono bg-white px-1.5 py-0.5 rounded border border-amber-200">http://localhost:8000</code>). Start with <code className="font-mono bg-white px-1.5 py-0.5 rounded border border-amber-200">docker compose up</code> or <code className="font-mono bg-white px-1.5 py-0.5 rounded border border-amber-200">uv run uvicorn orchestrator.app:app</code> to interact with live containers.
          </span>
        </div>
      )}

      {/* 4 Departments Grid (visible in DOM) */}
      <div className={`p-4 sm:p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 ${isExpanded ? 'block' : 'hidden'}`}>
        {DEPARTMENTS.map((dept) => {
          const currentStatus = faultStatuses[dept.name];
          const currentMode = currentStatus?.mode || 'none';
          const isUpdating = updatingDept === dept.name;

          return (
            <div
              key={dept.name}
              className={`p-4 rounded-2xl border text-xs transition-all flex flex-col justify-between ${
                currentMode !== 'none'
                  ? 'border-rose-300 bg-rose-50/50 shadow-xs'
                  : 'bg-slate-50/70 border-slate-200 hover:border-slate-300'
              }`}
            >
              <div>
                {/* Department Header */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center space-x-2">
                    <span className="shrink-0">{dept.icon}</span>
                    <span className="font-bold text-[#1d1d1f]">{dept.label}</span>
                  </div>
                  {getModeBadge(currentStatus)}
                </div>

                <p className="text-[11px] text-slate-500 mb-3 truncate font-normal">
                  {currentStatus?.message || `${dept.label} operating normally`}
                </p>
              </div>

              {/* Mode Control Buttons */}
              <div className="space-y-1.5 pt-2.5 border-t border-slate-200">
                <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider block">
                  Fault Mode:
                </span>
                <div className="grid grid-cols-2 gap-1.5 text-[11px]">
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'none')}
                    className={`apple-press px-2 py-1.5 rounded-xl font-semibold transition-all ${
                      currentMode === 'none'
                        ? 'bg-emerald-600 text-white shadow-xs'
                        : 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-100'
                    }`}
                  >
                    Healthy
                  </button>
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'offline')}
                    className={`apple-press px-2 py-1.5 rounded-xl font-semibold transition-all ${
                      currentMode === 'offline'
                        ? 'bg-rose-600 text-white shadow-xs'
                        : 'bg-white text-slate-700 border border-slate-200 hover:bg-rose-50 hover:text-rose-800'
                    }`}
                  >
                    Offline (503)
                  </button>
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'slow', 3000)}
                    className={`apple-press px-2 py-1.5 rounded-xl font-semibold transition-all ${
                      currentMode === 'slow'
                        ? 'bg-amber-600 text-white shadow-xs'
                        : 'bg-white text-slate-700 border border-slate-200 hover:bg-amber-50 hover:text-amber-800'
                    }`}
                  >
                    Slow (3.0s)
                  </button>
                  <button
                    type="button"
                    disabled={!isBackendOnline || isUpdating}
                    onClick={() => handleSetFault(dept.name, 'error')}
                    className={`apple-press px-2 py-1.5 rounded-xl font-semibold transition-all ${
                      currentMode === 'error'
                        ? 'bg-red-600 text-white shadow-xs'
                        : 'bg-white text-slate-700 border border-slate-200 hover:bg-red-50 hover:text-red-800'
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
