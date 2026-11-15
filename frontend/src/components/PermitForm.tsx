import React, { useState, useRef, useEffect } from 'react';
import { Permit, SamplePlanInfo, IntakeParseResponse } from '../types';
import { parseIntakePlan, fetchSamplePlans, fetchSamplePlan } from '../utils/api';
import { FIXTURE_SCENARIOS } from '../fixtures';
import {
  FileText,
  MapPin,
  User,
  Shield,
  Flame,
  Droplet,
  Home,
  Send,
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

const FALLBACK_SAMPLE_PERMITS: Record<string, Permit> = {
  residential_addition: FIXTURE_SCENARIOS.approved.permit,
  commercial_restaurant: FIXTURE_SCENARIOS.rejected.permit,
  industrial_warehouse: FIXTURE_SCENARIOS.timeout.permit,
};

export const PermitForm: React.FC<PermitFormProps> = ({
  permit,
  onChange,
  onSubmit,
  isSubmitting,
  mode,
}) => {
  // Intake Plan & PDF parsing state (Task T13)
  const [isParsingPlan, setIsParsingPlan] = useState(false);
  const [intakeResult, setIntakeResult] = useState<IntakeParseResponse | null>(null);
  const [intakeError, setIntakeError] = useState<string | null>(null);
  const [samplePlans, setSamplePlans] = useState<SamplePlanInfo[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
    } catch (err: any) {
      const fallbackPermit = FALLBACK_SAMPLE_PERMITS[planId];
      if (fallbackPermit && (err.message?.includes('404') || err.message?.includes('Failed to fetch sample plan'))) {
        onChange(fallbackPermit);
        setIntakeResult({
          permit: fallbackPermit,
          filename: `${planId}_plan.txt`,
          file_path: null,
          extracted_by: 'built-in-fixture-fallback',
          warnings: [],
        });
        setIntakeError(null);
      } else {
        setIntakeError(err.message || 'Failed to parse sample plan');
      }
    } finally {
      setIsParsingPlan(false);
    }
  };

  return (
    <div className="bg-white rounded-3xl border border-black/[0.06] shadow-xs overflow-hidden transition-all">
      {/* Header */}
      <div className="p-4 sm:p-5 border-b border-slate-100 bg-slate-50/70 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center space-x-3">
          <div className="p-2 rounded-xl bg-blue-50 text-[#0071e3] border border-blue-200/60 shadow-2xs">
            <FileText className="h-5 w-5" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-[#1d1d1f] tracking-tight">Permit Application Payload</h3>
            <p className="text-xs text-[#86868b] font-normal">
              Submitted permit parameters for Zoning, Fire, Water, and Structural review
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
            className="apple-press px-3.5 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-800 text-xs font-semibold flex items-center space-x-1.5 transition-all disabled:opacity-50 border border-slate-200 shadow-2xs"
          >
            {isParsingPlan ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-[#0071e3]" />
            ) : (
              <UploadCloud className="h-3.5 w-3.5 text-[#0071e3]" />
            )}
            <span>{isParsingPlan ? 'Parsing Plan...' : 'Upload Plan / PDF'}</span>
          </button>

          {/* Quick Sample Selector */}
          <select
            onChange={(e) => {
              if (e.target.value) handleSelectSample(e.target.value);
            }}
            defaultValue=""
            className="bg-white border border-slate-200 text-slate-800 text-xs rounded-xl px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500 shadow-2xs font-medium"
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

          {/* Check For Approval / Submit button */}
          <button
            type="button"
            disabled={isSubmitting}
            onClick={onSubmit}
            className="apple-press px-4 py-1.5 rounded-xl bg-[#0071e3] hover:bg-[#0077ed] active:bg-[#0062c4] text-white text-xs font-semibold flex items-center space-x-1.5 transition-all disabled:opacity-50 shadow-xs shadow-blue-500/20"
          >
            <Send className="h-3.5 w-3.5" />
            <span>{mode === 'live' ? 'Submit to Orchestrator' : 'Check For Approval'}</span>
          </button>
        </div>
      </div>

      {/* Intake Status Banner (Task T13) */}
      {intakeResult && (
        <div className="mx-4 sm:mx-5 mt-4 p-4 rounded-2xl bg-emerald-50 border border-emerald-200 text-xs text-emerald-950 flex items-start justify-between gap-3 shadow-xs">
          <div className="flex items-start space-x-3">
            <div className="p-1 rounded-xl bg-emerald-100 text-emerald-700 shrink-0 mt-0.5">
              <CheckCircle2 className="h-4 w-4" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-bold text-emerald-900 text-sm">Plan Parsed Successfully</span>
                <span
                  className={`px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold flex items-center space-x-1 ${
                    intakeResult.extracted_by.includes('gemini')
                      ? 'bg-purple-100 text-purple-800 border border-purple-200'
                      : 'bg-emerald-100 text-emerald-800 border border-emerald-200'
                  }`}
                >
                  <Sparkles className="h-3 w-3" />
                  <span>{intakeResult.extracted_by}</span>
                </span>
                <span className="text-slate-600 font-mono text-[11px]">
                  Source: {intakeResult.filename}
                </span>
              </div>
              <p className="text-slate-700 mt-1 font-normal">
                Extracted all parameters for Zoning, Fire, Water, and Structural reviews into the permit payload.
              </p>
              {intakeResult.warnings && intakeResult.warnings.length > 0 && (
                <div className="mt-1 text-[11px] text-amber-800 font-medium">
                  Note: {intakeResult.warnings.join('; ')}
                </div>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setIntakeResult(null)}
            className="apple-press text-slate-400 hover:text-slate-700 text-xs font-bold px-1.5 py-0.5 rounded-lg hover:bg-slate-200/50"
          >
            ✕
          </button>
        </div>
      )}

      {intakeError && (
        <div className="mx-4 sm:mx-5 mt-4 p-4 rounded-2xl bg-rose-50 border border-rose-200 text-xs text-rose-950 flex items-center justify-between shadow-xs">
          <div className="flex items-center space-x-2.5">
            <AlertCircle className="h-4 w-4 text-rose-600 shrink-0" />
            <span className="font-medium">{intakeError}</span>
          </div>
          <button
            type="button"
            onClick={() => setIntakeError(null)}
            className="apple-press text-rose-500 hover:text-rose-800 text-xs font-bold px-1.5 py-0.5 rounded-lg hover:bg-rose-100/50"
          >
            ✕
          </button>
        </div>
      )}

      {/* Content: Clean Visual Cards */}
      <div className="p-4 sm:p-5">
        <div className="space-y-4 text-xs">
          {/* Project & Applicant Details */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
            <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200 shadow-2xs">
              <div className="flex items-center space-x-1.5 text-slate-700 font-semibold mb-2">
                <MapPin className="h-4 w-4 text-[#0071e3]" />
                <span>Property &amp; Title</span>
              </div>
              <p className="text-[#1d1d1f] font-bold text-sm mb-1">{permit.project_title}</p>
              <p className="text-slate-600">
                {permit.property_address?.street}, {permit.property_address?.city},{' '}
                {permit.property_address?.state} {permit.property_address?.zip}
              </p>
              {permit.estimated_cost_usd && (
                <p className="text-slate-600 mt-1">
                  Estimated Cost:{' '}
                  <span className="text-emerald-700 font-mono font-bold">
                    ${permit.estimated_cost_usd.toLocaleString()}
                  </span>
                </p>
              )}
            </div>

            <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200 shadow-2xs">
              <div className="flex items-center space-x-1.5 text-slate-700 font-semibold mb-2">
                <User className="h-4 w-4 text-[#0071e3]" />
                <span>Applicant Information</span>
              </div>
              <p className="text-[#1d1d1f] font-bold text-sm mb-1">{permit.applicant?.name}</p>
              <p className="text-slate-600">
                Role: <span className="text-slate-800 font-medium capitalize">{permit.applicant?.role}</span>
              </p>
              <p className="text-slate-600">Email: {permit.applicant?.email}</p>
            </div>
          </div>

          {/* 4 Departments Data Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
            {/* Zoning */}
            <div className="p-4 rounded-2xl bg-indigo-50/40 border border-indigo-100 shadow-2xs">
              <div className="flex items-center space-x-1.5 text-indigo-900 font-bold mb-2.5">
                <Home className="h-4 w-4 text-indigo-600" />
                <span>Zoning Data</span>
              </div>
              <div className="space-y-1.5 text-slate-600">
                <div>
                  Zone: <span className="text-[#1d1d1f] font-mono font-bold">{permit.zoning?.zone_code}</span>
                </div>
                <div>
                  Height: <span className="text-[#1d1d1f] font-medium">{permit.zoning?.building_height_ft} ft</span>
                </div>
                <div>
                  Footprint:{' '}
                  <span className="text-[#1d1d1f] font-medium">{permit.zoning?.building_footprint_sqft} sqft</span>
                </div>
                <div>
                  Rear Setback:{' '}
                  <span className="text-[#1d1d1f] font-medium">{permit.zoning?.setback_rear_ft} ft</span>
                </div>
              </div>
            </div>

            {/* Fire */}
            <div className="p-4 rounded-2xl bg-rose-50/40 border border-rose-100 shadow-2xs">
              <div className="flex items-center space-x-1.5 text-rose-900 font-bold mb-2.5">
                <Flame className="h-4 w-4 text-rose-600" />
                <span>Fire Data</span>
              </div>
              <div className="space-y-1.5 text-slate-600">
                <div>
                  Occupancy:{' '}
                  <span className="text-[#1d1d1f] font-medium capitalize">{permit.fire?.occupancy_type}</span>
                </div>
                <div>
                  Occupant Load: <span className="text-[#1d1d1f] font-medium">{permit.fire?.occupant_load}</span>
                </div>
                <div>
                  Sprinklers:{' '}
                  <span
                    className={`font-semibold ${
                      permit.fire?.has_sprinkler_system ? 'text-emerald-700' : 'text-rose-700'
                    }`}
                  >
                    {permit.fire?.has_sprinkler_system ? 'Yes' : 'No'}
                  </span>
                </div>
                <div>
                  Exits: <span className="text-[#1d1d1f] font-medium">{permit.fire?.egress_exits_count}</span>
                </div>
              </div>
            </div>

            {/* Water */}
            <div className="p-4 rounded-2xl bg-sky-50/40 border border-sky-100 shadow-2xs">
              <div className="flex items-center space-x-1.5 text-sky-900 font-bold mb-2.5">
                <Droplet className="h-4 w-4 text-sky-600" />
                <span>Water Data</span>
              </div>
              <div className="space-y-1.5 text-slate-600">
                <div>
                  Meter:{' '}
                  <span className="text-[#1d1d1f] font-medium">{permit.water?.water_meter_size_inches} in</span>
                </div>
                <div>
                  Daily Usage:{' '}
                  <span className="text-[#1d1d1f] font-medium">{permit.water?.daily_water_usage_gpd} GPD</span>
                </div>
                <div>
                  Backflow Preventer:{' '}
                  <span
                    className={`font-semibold ${
                      permit.water?.has_backflow_preventer ? 'text-emerald-700' : 'text-amber-700'
                    }`}
                  >
                    {permit.water?.has_backflow_preventer ? 'Yes' : 'No'}
                  </span>
                </div>
                <div>
                  Stormwater Plan:{' '}
                  <span
                    className={`font-semibold ${
                      permit.water?.has_stormwater_plan ? 'text-emerald-700' : 'text-amber-700'
                    }`}
                  >
                    {permit.water?.has_stormwater_plan ? 'Yes' : 'No'}
                  </span>
                </div>
              </div>
            </div>

            {/* Structural */}
            <div className="p-4 rounded-2xl bg-emerald-50/40 border border-emerald-100 shadow-2xs">
              <div className="flex items-center space-x-1.5 text-emerald-900 font-bold mb-2.5">
                <Shield className="h-4 w-4 text-emerald-600" />
                <span>Structural Data</span>
              </div>
              <div className="space-y-1.5 text-slate-600">
                <div>
                  Stories: <span className="text-[#1d1d1f] font-medium">{permit.structural?.stories_count}</span>
                </div>
                <div>
                  Construction:{' '}
                  <span className="text-[#1d1d1f] font-medium capitalize">
                    {permit.structural?.construction_type?.replace('_', ' ')}
                  </span>
                </div>
                <div>
                  Seismic Category:{' '}
                  <span className="text-[#1d1d1f] font-mono font-bold">
                    {permit.structural?.seismic_design_category}
                  </span>
                </div>
                <div>
                  Foundation:{' '}
                  <span className="text-[#1d1d1f] font-medium capitalize">
                    {permit.structural?.foundation_type?.replace('_', ' ')}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
