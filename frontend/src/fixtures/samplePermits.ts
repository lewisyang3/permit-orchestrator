import { Permit, EvidenceSpan } from '../types';
import { FIXTURE_SCENARIOS } from './index';

const makeSpan = (field_path: string, quote: string, line: number, page: number = 1): EvidenceSpan => ({
  field_path,
  quote,
  page,
  line,
  bboxes: [{ x0: 40, top: 200 + line * 15, x1: 280, bottom: 215 + line * 15 }],
  status: 'verified',
  value_supported: true,
  method: 'deterministic',
});

export const RESIDENTIAL_ADDITION_PERMIT: Permit = {
  ...FIXTURE_SCENARIOS.approved.permit,
  project_title: 'Single Family Residential Rear Addition',
  property_address: {
    street: '742 Evergreen Terrace',
    city: 'San Jose',
    state: 'CA',
    zip: '95125',
  },
  source_document_id: 'sample-residential-rear-addition',
  evidence: {
    'project_title': makeSpan('project_title', 'Project Title: Single Family Residential Rear Addition', 2),
    'property_address.street': makeSpan('property_address.street', 'Address: 742 Evergreen Terrace', 8),
    'property_address.city': makeSpan('property_address.city', 'City: San Jose', 9),
    'property_address.state': makeSpan('property_address.state', 'State: CA', 10),
    'applicant.name': makeSpan('applicant.name', 'Name: Homer Simpson', 13),
    'applicant.role': makeSpan('applicant.role', 'Role: homeowner', 14),
    'zoning.zone_code': makeSpan('zoning.zone_code', 'Zone District: R-1 (Low Density Residential)', 19),
    'zoning.building_height_ft': makeSpan('zoning.building_height_ft', 'Building Height: 16.5 ft', 20),
    'zoning.lot_area_sqft': makeSpan('zoning.lot_area_sqft', 'Lot Area: 8,400 sq ft', 21),
    'zoning.building_footprint_sqft': makeSpan('zoning.building_footprint_sqft', 'Proposed Total Footprint: 2,350 sq ft', 23),
    'zoning.setback_front_ft': makeSpan('zoning.setback_front_ft', 'Front Setback: 25.0 ft', 24),
    'zoning.setback_rear_ft': makeSpan('zoning.setback_rear_ft', 'Rear Setback: 32.0 ft', 25),
    'zoning.setback_side_ft': makeSpan('zoning.setback_side_ft', 'Side Setback: 8.5 ft', 26),
    'fire.occupancy_type': makeSpan('fire.occupancy_type', 'Occupancy Type: residential', 29),
    'fire.occupant_load': makeSpan('fire.occupant_load', 'Occupant Load: 5 persons', 30),
    'fire.egress_exits_count': makeSpan('fire.egress_exits_count', 'Egress Exits Count: 2 exterior exit doors', 33),
    'fire.distance_to_hydrant_ft': makeSpan('fire.distance_to_hydrant_ft', 'Distance to Nearest Fire Hydrant: 140 ft', 34),
    'water.water_meter_size_inches': makeSpan('water.water_meter_size_inches', 'Water Service Meter Size: 0.75 inches', 37),
    'water.daily_water_usage_gpd': makeSpan('water.daily_water_usage_gpd', 'Estimated Daily Water Usage: 280 gpd', 38),
    'structural.stories_count': makeSpan('structural.stories_count', 'Stories: 1 story', 43),
    'structural.construction_type': makeSpan('structural.construction_type', 'Framing Construction: wood_frame (Type V-B)', 44),
    'structural.seismic_design_category': makeSpan('structural.seismic_design_category', 'Seismic Design Category: C', 45),
    'structural.foundation_type': makeSpan('structural.foundation_type', 'Foundation Type: continuous_spread_footing', 46),
  },
};
