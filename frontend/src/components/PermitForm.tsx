import React, { useState } from 'react';
import { Permit } from '../types';
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

        <div className="flex items-center space-x-2">
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
