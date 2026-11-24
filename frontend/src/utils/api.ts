import {
  AdminFaultResponse,
  FaultControlRequest,
  FaultControlResponse,
  DepartmentName,
  Permit,
  SubmitPermitResponse,
  PermitStatusResponse,
  IntakeParseRequest,
  IntakeParseResponse,
  SamplePlanInfo,
} from '../types';

const BASE_URL = '';

/** URL of an uploaded plan, for showing cited evidence. */
export function documentUrl(documentId: string): string {
  return `${BASE_URL}/intake/documents/${encodeURIComponent(documentId)}`;
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/health`);
    if (!res.ok) return false;
    const contentType = res.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) return false;
    const data = await res.json();
    return data.status === 'healthy';
  } catch {
    return false;
  }
}

export async function fetchAdminFaults(): Promise<AdminFaultResponse> {
  const res = await fetch(`${BASE_URL}/admin/fault`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to fetch admin fault status (${res.status}): ${text}`);
  }
  return res.json();
}

export async function setDepartmentFault(req: FaultControlRequest): Promise<FaultControlResponse> {
  const res = await fetch(`${BASE_URL}/admin/fault`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Fault injection failed (${res.status}): ${text}`);
  }
  return res.json();
}

export async function restoreAllDepartmentsHealthy(): Promise<Record<DepartmentName, FaultControlResponse>> {
  const departments: DepartmentName[] = ['zoning', 'fire', 'water', 'structural'];
  const responses = await Promise.all(
    departments.map((dept) =>
      setDepartmentFault({ department: dept, mode: 'none', delay_ms: 0 })
    )
  );
  const results: Partial<Record<DepartmentName, FaultControlResponse>> = {};
  departments.forEach((dept, idx) => {
    results[dept] = responses[idx];
  });
  return results as Record<DepartmentName, FaultControlResponse>;
}

export async function submitPermit(permit: Permit): Promise<SubmitPermitResponse> {
  const res = await fetch(`${BASE_URL}/submit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(permit),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Submit permit failed (${res.status}): ${text}`);
  }
  return res.json();
}

export async function getPermitStatus(permitId: string): Promise<PermitStatusResponse> {
  const res = await fetch(`${BASE_URL}/permits/${permitId}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Get permit status failed (${res.status}): ${text}`);
  }
  return res.json();
}

export async function parseIntakePlan(req: IntakeParseRequest): Promise<IntakeParseResponse> {
  const res = await fetch(`${BASE_URL}/intake/parse`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Plan parsing failed (${res.status}): ${text}`);
  }
  return res.json();
}

export async function fetchSamplePlans(): Promise<SamplePlanInfo[]> {
  const res = await fetch(`${BASE_URL}/intake/sample-plans`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to fetch sample plans (${res.status}): ${text}`);
  }
  return res.json();
}

export async function fetchSamplePlan(planId: string): Promise<SamplePlanInfo> {
  const res = await fetch(`${BASE_URL}/intake/sample-plans/${planId}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Failed to fetch sample plan ${planId} (${res.status}): ${text}`);
  }
  return res.json();
}
