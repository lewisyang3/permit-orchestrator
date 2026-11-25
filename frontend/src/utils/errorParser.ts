/**
 * Utilities for formatting architectural plan parsing errors into clean,
 * human-readable error messages with categorized missing/invalid fields.
 */

export interface FormattedPlanError {
  title: string;
  message: string;
  missingFields?: string[];
  invalidFields?: string[];
  rawMessage?: string;
}

export interface CategorizedField {
  path: string;
  label: string;
  category: string;
}

export const FIELD_METADATA: Record<string, { label: string; category: string }> = {
  // Applicant
  'applicant.name': { label: 'Applicant Name', category: 'Applicant' },
  'applicant.role': { label: 'Role (homeowner/contractor)', category: 'Applicant' },
  'applicant.email': { label: 'Email Address', category: 'Applicant' },
  'applicant.phone': { label: 'Phone Number', category: 'Applicant' },

  // Property & Zoning
  'property_address.street': { label: 'Street Address', category: 'Zoning & Location' },
  'property_address.city': { label: 'City', category: 'Zoning & Location' },
  'property_address.state': { label: 'State', category: 'Zoning & Location' },
  'property_address.zip': { label: 'Zip Code', category: 'Zoning & Location' },
  'zoning.zone_code': { label: 'Zone District Code', category: 'Zoning & Location' },
  'zoning.building_height_ft': { label: 'Building Height (ft)', category: 'Zoning & Location' },
  'zoning.lot_area_sqft': { label: 'Lot Area (sqft)', category: 'Zoning & Location' },
  'zoning.building_footprint_sqft': { label: 'Footprint (sqft)', category: 'Zoning & Location' },
  'zoning.setback_front_ft': { label: 'Front Setback (ft)', category: 'Zoning & Location' },
  'zoning.setback_rear_ft': { label: 'Rear Setback (ft)', category: 'Zoning & Location' },
  'zoning.setback_side_ft': { label: 'Side Setback (ft)', category: 'Zoning & Location' },

  // Fire Life Safety
  'fire.occupancy_type': { label: 'Occupancy Type', category: 'Fire Life Safety' },
  'fire.occupant_load': { label: 'Occupant Load', category: 'Fire Life Safety' },
  'fire.has_sprinkler_system': { label: 'Automatic Sprinklers', category: 'Fire Life Safety' },
  'fire.egress_exits_count': { label: 'Egress Exits Count', category: 'Fire Life Safety' },
  'fire.distance_to_hydrant_ft': { label: 'Hydrant Distance (ft)', category: 'Fire Life Safety' },
  'fire.has_fire_alarm': { label: 'Fire Alarm System', category: 'Fire Life Safety' },

  // Water & Utilities
  'water.water_meter_size_inches': { label: 'Water Meter Size (in)', category: 'Water & Utilities' },
  'water.daily_water_usage_gpd': { label: 'Daily Water Usage (GPD)', category: 'Water & Utilities' },
  'water.has_backflow_preventer': { label: 'Backflow Preventer', category: 'Water & Utilities' },
  'water.has_stormwater_plan': { label: 'Stormwater Plan', category: 'Water & Utilities' },

  // Structural Engineering
  'structural.stories_count': { label: 'Stories Count', category: 'Structural Engineering' },
  'structural.construction_type': { label: 'Construction Framing Type', category: 'Structural Engineering' },
  'structural.seismic_design_category': { label: 'Seismic Design Category', category: 'Structural Engineering' },
  'structural.foundation_type': { label: 'Foundation Type', category: 'Structural Engineering' },

  // General
  'project_title': { label: 'Project Title', category: 'General' },
  'project_description': { label: 'Project Description', category: 'General' },
  'estimated_cost_usd': { label: 'Estimated Cost ($)', category: 'General' },
};

export function formatField(path: string): CategorizedField {
  if (FIELD_METADATA[path]) {
    return { path, ...FIELD_METADATA[path] };
  }
  const parts = path.split('.');
  const rawCat = parts.length > 1 ? parts[0] : 'General';
  const category =
    rawCat === 'fire'
      ? 'Fire Life Safety'
      : rawCat === 'water'
      ? 'Water & Utilities'
      : rawCat === 'structural'
      ? 'Structural Engineering'
      : rawCat === 'zoning'
      ? 'Zoning & Location'
      : rawCat === 'applicant'
      ? 'Applicant'
      : 'General';
  const rawField = parts.length > 1 ? parts[1] : parts[0];
  const label = rawField
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return { path, label, category };
}

export function groupMissingFields(paths: string[]): Record<string, CategorizedField[]> {
  const groups: Record<string, CategorizedField[]> = {};
  for (const p of paths) {
    const item = formatField(p);
    if (!groups[item.category]) {
      groups[item.category] = [];
    }
    groups[item.category].push(item);
  }
  return groups;
}

/**
 * One invalid-field entry as display text. The backend sends Pydantic-style
 * objects ({loc, msg, type}); strings are accepted as-is.
 */
export function formatFieldError(e: unknown): string {
  if (typeof e === 'string') return e;
  if (e && typeof e === 'object' && ('loc' in e || 'msg' in e)) {
    const { loc, msg } = e as { loc?: unknown; msg?: unknown };
    const path = Array.isArray(loc) ? loc.map(String).join('.') : '';
    const text = typeof msg === 'string' ? msg : 'Invalid value';
    if (!path) return text;
    const label = FIELD_METADATA[path] ? FIELD_METADATA[path].label : path;
    return `${label}: ${text}`;
  }
  return JSON.stringify(e);
}

export function parsePlanError(error: unknown): FormattedPlanError {
  if (!error) {
    return {
      title: 'Plan Parsing Failed',
      message: 'An unknown error occurred while parsing the plan document.',
    };
  }

  const rawStr = error instanceof Error ? error.message : String(error);

  // Check if rawStr contains a JSON payload
  const jsonMatch = rawStr.match(/\{[\s\S]*\}/);
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[0]);
      const detail = parsed.detail || parsed;

      if (typeof detail === 'object' && detail !== null) {
        if (detail.error === 'MISSING_REQUIRED_FIELDS' || detail.missing_fields) {
          const fields: string[] = Array.isArray(detail.missing_fields)
            ? detail.missing_fields.filter((f: unknown): f is string => typeof f === 'string')
            : [];
          return {
            title: 'Plan Incomplete: Missing Required Fields',
            message: `The uploaded plan document is missing ${fields.length || 'several'} required municipal parameters. Review the missing fields below and update your plan:`,
            missingFields: fields,
            rawMessage: rawStr,
          };
        }

        if (detail.error === 'INVALID_FIELD_VALUES' || detail.errors) {
          const errors: string[] = Array.isArray(detail.errors)
            ? detail.errors.map(formatFieldError)
            : [];
          return {
            title: 'Invalid Plan Parameters',
            message: detail.message || 'Some extracted values could not be validated:',
            invalidFields: errors,
            rawMessage: rawStr,
          };
        }

        if (detail.message) {
          return {
            title: 'Plan Parsing Failed',
            message: detail.message,
            rawMessage: rawStr,
          };
        }
      } else if (typeof detail === 'string') {
        const cleanMsg = detail.replace(/^Plan parsing failed:\s*/i, '');
        return {
          title: 'Plan Parsing Failed',
          message: cleanMsg,
          rawMessage: rawStr,
        };
      }
    } catch {
      // JSON parse failed; fall through to raw string handling
    }
  }

  // Strip HTTP status prefix if present e.g. "Plan parsing failed (400): "
  const cleaned = rawStr.replace(/^Plan parsing failed\s*\(\d+\):\s*/i, '');

  return {
    title: 'Plan Parsing Failed',
    message: cleaned,
    rawMessage: rawStr,
  };
}
