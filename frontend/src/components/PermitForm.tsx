import React, { useState, useRef, useEffect } from 'react';
import { Permit, SamplePlanInfo, IntakeParseResponse } from '../types';
import { parseIntakePlan, fetchSamplePlans, fetchSamplePlan } from '../utils/api';
import {
  FileText,
  MapPin,
  User,
  Shield,
  Flame,
  Droplet,
  Home,
  Send,
  Code,
  UploadCloud,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  Loader2,
} from 'lucide-react';

interface PermitFormProps {
  permit: Permit;
  onChange: (updated: Permit) => void;
  onSubmit: () => void;
  isSubmitting: boolean;
  mode: 'fixture' | 'live';
}

export const PermitForm: React.FC<PermitFormProps> = ({
  permit,
  onChange,
  onSubmit,
  isSubmitting,
  mode,
}) => {
  const [viewMode, setViewMode] = useState<'visual' | 'json'>('visual');
  const [rawJson, setRawJson] = useState(JSON.stringify(permit, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Intake Plan & PDF parsing state (Task T13)
  const [isParsingPlan, setIsParsingPlan] = useState(false);
  const [intakeResult, setIntakeResult] = useState<IntakeParseResponse | null>(null);
  const [intakeError, setIntakeError] = useState<string | null>(null);
  const [samplePlans, setSamplePlans] = useState<SamplePlanInfo[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Keep rawJson in sync with permit updates
  useEffect(() => {
    setRawJson(JSON.stringify(permit, null, 2));
  }, [permit]);

  // Load sample plan catalog on mount
  useEffect(() => {
    let mounted = true;
    fetchSamplePlans()
      .then((plans) => {
        if (mounted && plans && plans.length > 0) {
          setSamplePlans(plans);
        }
      })
      .catch(() => {
        // Fallback default sample plans when backend offline
        if (mounted) {
          setSamplePlans([
            {
              id: 'residential_addition',
              title: 'Residential Rear Addition',
              filename: 'residential_rear_addition_plan.txt',
              description: 'Single family residential addition (R-1, 1-story wood frame).',
              mime_type: 'text/plain',
            },
            {
              id: 'commercial_restaurant',
              title: 'Commercial Restaurant Patio',
              filename: 'commercial_restaurant_plan.txt',
              description: 'Commercial restaurant renovation (C-1, missing sprinklers).',
              mime_type: 'text/plain',
            },
            {
              id: 'industrial_warehouse',
              title: 'Storage Warehouse Modernization',
              filename: 'warehouse_modernization_plan.txt',
              description: 'Industrial logistics warehouse (M-1, deep pile foundation).',
              mime_type: 'text/plain',
            },
          ]);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  const handleJsonChange = (val: string) => {
    setRawJson(val);
    try {
      const parsed = JSON.parse(val);
      setJsonError(null);
      onChange(parsed);
    } catch (err: any) {
      setJsonError(err.message);
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsParsingPlan(true);
    setIntakeError(null);

    const isPdf = file.name.toLowerCase().endsWith('.pdf');
    const reader = new FileReader();

    if (isPdf) {
      reader.onload = async () => {
        try {
          const dataUrl = reader.result as string;
          const base64Data = dataUrl.includes(',') ? dataUrl.split(',')[1] : dataUrl;
          const res = await parseIntakePlan({
            filename: file.name,
            content_base64: base64Data,
            mime_type: 'application/pdf',
          });
          setIntakeResult(res);
          onChange(res.permit);
          setViewMode('visual');
        } catch (err: any) {
          setIntakeError(err.message || 'Failed to parse plan PDF');
        } finally {
          setIsParsingPlan(false);
          if (fileInputRef.current) fileInputRef.current.value = '';
        }
      };
      reader.readAsDataURL(file);
    } else {
      reader.onload = async () => {
        try {
          const text = reader.result as string;
          const res = await parseIntakePlan({
            filename: file.name,
            content_text: text,
            mime_type: 'text/plain',
          });
          setIntakeResult(res);
          onChange(res.permit);
          setViewMode('visual');
        } catch (err: any) {
          setIntakeError(err.message || 'Failed to parse plan file');
        } finally {
          setIsParsingPlan(false);
          if (fileInputRef.current) fileInputRef.current.value = '';
        }
      };
      reader.readAsText(file);
    }
  };

  const handleSelectSample = async (planId: string) => {
    if (!planId) return;
    setIsParsingPlan(true);
    setIntakeError(null);

    try {
      const detail = await fetchSamplePlan(planId);
      const res = await parseIntakePlan({
        filename: detail.filename,
        content_text: detail.sample_text,
        mime_type: detail.mime_type,
      });
      setIntakeResult(res);
      onChange(res.permit);
      setViewMode('visual');
    } catch (err: any) {
      setIntakeError(err.message || 'Failed to parse sample plan');
    } finally {
      setIsParsingPlan(false);
    }
  };

  return (
    <div className="bg-slate-900/90 rounded-xl border border-slate-800 shadow-xl overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-slate-800 bg-slate-950/70 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center space-x-2">
          <FileText className="h-5 w-5 text-emerald-400" />
          <div>
            <h3 className="text-sm font-bold text-white">Permit Application Payload</h3>
            <p className="text-xs text-slate-400">
              Submitted permit JSON with parameters for Zoning, Fire, Water, and Structural
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Hidden File Input for PDF / Plan uploads */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,.md"
            onChange={handleFileUpload}
            className="hidden"
          />

          {/* Upload Plan / PDF button (Task T13) */}
          <button
            type="button"
            disabled={isParsingPlan}
            onClick={() => fileInputRef.current?.click()}
            className="px-3 py-1 rounded-lg bg-indigo-600/80 hover:bg-indigo-500 text-white text-xs font-semibold flex items-center space-x-1.5 transition-colors disabled:opacity-50 shadow-sm border border-indigo-500/40"
          >
            {isParsingPlan ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <UploadCloud className="h-3.5 w-3.5" />
            )}
            <span>{isParsingPlan ? 'Parsing Plan...' : 'Upload Plan / PDF'}</span>
          </button>

          {/* Quick Sample Selector */}
          <select
            onChange={(e) => {
              if (e.target.value) handleSelectSample(e.target.value);
            }}
            defaultValue=""
            className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg px-2.5 py-1 focus:outline-none focus:border-indigo-500"
          >
            <option value="" disabled>
              Load Sample Plan...
            </option>
            {samplePlans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>

          {/* Toggle Visual / JSON */}
          <div className="bg-slate-800 p-0.5 rounded-lg border border-slate-700 flex text-xs">
            <button
              type="button"
              onClick={() => {
                setViewMode('visual');
                setRawJson(JSON.stringify(permit, null, 2));
              }}
              className={`px-2.5 py-1 rounded font-medium ${
                viewMode === 'visual' ? 'bg-slate-700 text-white' : 'text-slate-400'
              }`}
            >
              Visual Cards
            </button>
            <button
              type="button"
              onClick={() => {
                setViewMode('json');
                setRawJson(JSON.stringify(permit, null, 2));
              }}
              className={`px-2.5 py-1 rounded font-medium flex items-center space-x-1 ${
                viewMode === 'json' ? 'bg-slate-700 text-white' : 'text-slate-400'
              }`}
            >
              <Code className="h-3 w-3" />
              <span>JSON</span>
            </button>
          </div>

          {/* Submit button */}
          <button
            type="button"
            disabled={isSubmitting || !!jsonError}
            onClick={onSubmit}
            className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center space-x-1.5 transition-colors disabled:opacity-50 shadow-sm"
          >
            <Send className="h-3.5 w-3.5" />
            <span>{mode === 'live' ? 'Submit to Orchestrator' : 'Run Saga Simulation'}</span>
          </button>
        </div>
      </div>

      {/* Intake Status Banner (Task T13) */}
      {intakeResult && (
        <div className="mx-4 mt-3 p-3 rounded-lg bg-slate-950/80 border border-emerald-500/40 text-xs text-slate-200 flex items-start justify-between gap-3 shadow-inner">
          <div className="flex items-start space-x-2.5">
            <div className="p-1 rounded-md bg-emerald-500/20 text-emerald-400 shrink-0 mt-0.5">
              <CheckCircle2 className="h-4 w-4" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-emerald-300">Plan Parsed Successfully</span>
                <span
                  className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold flex items-center space-x-1 ${
                    intakeResult.extracted_by.includes('gemini')
                      ? 'bg-purple-500/20 text-purple-300 border border-purple-500/40'
                      : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                  }`}
                >
                  <Sparkles className="h-2.5 w-2.5" />
                  <span>{intakeResult.extracted_by}</span>
                </span>
                <span className="text-slate-400 font-mono text-[11px]">
                  Source: {intakeResult.filename}
                </span>
              </div>
              <p className="text-slate-400 mt-1">
                Extracted all parameters for Zoning, Fire, Water, and Structural reviews into the permit payload.
              </p>
              {intakeResult.warnings && intakeResult.warnings.length > 0 && (
                <div className="mt-1 text-[11px] text-amber-300">
                  Note: {intakeResult.warnings.join('; ')}
                </div>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setIntakeResult(null)}
            className="text-slate-500 hover:text-slate-300 text-xs font-bold px-1"
          >
            ✕
          </button>
        </div>
      )}

      {intakeError && (
        <div className="mx-4 mt-3 p-3 rounded-lg bg-rose-950/50 border border-rose-500/40 text-xs text-rose-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <AlertCircle className="h-4 w-4 text-rose-400 shrink-0" />
            <span>{intakeError}</span>
          </div>
          <button
            type="button"
            onClick={() => setIntakeError(null)}
            className="text-rose-400 hover:text-rose-200 text-xs font-bold px-1"
          >
            ✕
          </button>
        </div>
      )}

      {/* Content */}
      <div className="p-4">
        {viewMode === 'json' ? (
          <div>
            {jsonError && (
              <div className="mb-2 p-2 rounded bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-mono">
                JSON Error: {jsonError}
              </div>
            )}
            <textarea
              value={rawJson}
              onChange={(e) => handleJsonChange(e.target.value)}
              rows={16}
              className="w-full bg-slate-950 font-mono text-xs text-slate-200 p-3 rounded-lg border border-slate-800 focus:outline-none focus:border-emerald-500"
            />
          </div>
        ) : (
          <div className="space-y-4 text-xs">
            {/* Project & Applicant Details */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <div className="flex items-center space-x-1.5 text-slate-300 font-semibold mb-2">
                  <MapPin className="h-4 w-4 text-emerald-400" />
                  <span>Property &amp; Title</span>
                </div>
                <p className="text-slate-100 font-bold text-sm mb-1">{permit.project_title}</p>
                <p className="text-slate-400">
                  {permit.property_address?.street}, {permit.property_address?.city},{' '}
                  {permit.property_address?.state} {permit.property_address?.zip}
                </p>
                {permit.estimated_cost_usd && (
                  <p className="text-slate-400 mt-1">
                    Estimated Cost:{' '}
                    <span className="text-emerald-400 font-mono font-medium">
                      ${permit.estimated_cost_usd.toLocaleString()}
                    </span>
                  </p>
                )}
              </div>

              <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <div className="flex items-center space-x-1.5 text-slate-300 font-semibold mb-2">
                  <User className="h-4 w-4 text-sky-400" />
                  <span>Applicant Information</span>
                </div>
                <p className="text-slate-100 font-bold text-sm mb-1">{permit.applicant?.name}</p>
                <p className="text-slate-400">
                  Role: <span className="text-slate-200 capitalize">{permit.applicant?.role}</span>
                </p>
                <p className="text-slate-400">Email: {permit.applicant?.email}</p>
              </div>
            </div>

            {/* 4 Departments Data Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {/* Zoning */}
              <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <div className="flex items-center space-x-1.5 text-slate-200 font-semibold mb-2">
                  <Home className="h-4 w-4 text-indigo-400" />
                  <span>Zoning Data</span>
                </div>
                <div className="space-y-1 text-slate-400">
                  <div>
                    Zone: <span className="text-slate-200 font-mono">{permit.zoning?.zone_code}</span>
                  </div>
                  <div>
                    Height: <span className="text-slate-200">{permit.zoning?.building_height_ft} ft</span>
                  </div>
                  <div>
                    Footprint:{' '}
                    <span className="text-slate-200">{permit.zoning?.building_footprint_sqft} sqft</span>
                  </div>
                  <div>
                    Rear Setback:{' '}
                    <span className="text-slate-200">{permit.zoning?.setback_rear_ft} ft</span>
                  </div>
                </div>
              </div>

              {/* Fire */}
              <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <div className="flex items-center space-x-1.5 text-slate-200 font-semibold mb-2">
                  <Flame className="h-4 w-4 text-rose-400" />
                  <span>Fire Data</span>
                </div>
                <div className="space-y-1 text-slate-400">
                  <div>
                    Occupancy:{' '}
                    <span className="text-slate-200 capitalize">{permit.fire?.occupancy_type}</span>
                  </div>
                  <div>
                    Occupant Load: <span className="text-slate-200">{permit.fire?.occupant_load}</span>
                  </div>
                  <div>
                    Sprinklers:{' '}
                    <span
                      className={
                        permit.fire?.has_sprinkler_system ? 'text-emerald-400' : 'text-rose-400'
                      }
                    >
                      {permit.fire?.has_sprinkler_system ? 'Yes' : 'No'}
                    </span>
                  </div>
                  <div>
                    Exits: <span className="text-slate-200">{permit.fire?.egress_exits_count}</span>
                  </div>
                </div>
              </div>

              {/* Water */}
              <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <div className="flex items-center space-x-1.5 text-slate-200 font-semibold mb-2">
                  <Droplet className="h-4 w-4 text-sky-400" />
                  <span>Water Data</span>
                </div>
                <div className="space-y-1 text-slate-400">
                  <div>
                    Meter:{' '}
                    <span className="text-slate-200">{permit.water?.water_meter_size_inches} in</span>
                  </div>
                  <div>
                    Daily Usage:{' '}
                    <span className="text-slate-200">{permit.water?.daily_water_usage_gpd} GPD</span>
                  </div>
                  <div>
                    Backflow Preventer:{' '}
                    <span
                      className={
                        permit.water?.has_backflow_preventer ? 'text-emerald-400' : 'text-amber-400'
                      }
                    >
                      {permit.water?.has_backflow_preventer ? 'Yes' : 'No'}
                    </span>
                  </div>
                  <div>
                    Stormwater Plan:{' '}
                    <span
                      className={
                        permit.water?.has_stormwater_plan ? 'text-emerald-400' : 'text-amber-400'
                      }
                    >
                      {permit.water?.has_stormwater_plan ? 'Yes' : 'No'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Structural */}
              <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <div className="flex items-center space-x-1.5 text-slate-200 font-semibold mb-2">
                  <Shield className="h-4 w-4 text-emerald-400" />
                  <span>Structural Data</span>
                </div>
                <div className="space-y-1 text-slate-400">
                  <div>
                    Stories: <span className="text-slate-200">{permit.structural?.stories_count}</span>
                  </div>
                  <div>
                    Construction:{' '}
                    <span className="text-slate-200 capitalize">
                      {permit.structural?.construction_type?.replace('_', ' ')}
                    </span>
                  </div>
                  <div>
                    Seismic Category:{' '}
                    <span className="text-slate-200 font-mono">
                      {permit.structural?.seismic_design_category}
                    </span>
                  </div>
                  <div>
                    Foundation:{' '}
                    <span className="text-slate-200 capitalize">
                      {permit.structural?.foundation_type?.replace('_', ' ')}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
