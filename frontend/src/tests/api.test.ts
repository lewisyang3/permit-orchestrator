import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  checkBackendHealth,
  fetchAdminFaults,
  setDepartmentFault,
  restoreAllDepartmentsHealthy,
  submitPermit,
  getPermitStatus,
  parseIntakePlan,
  fetchSamplePlans,
  fetchSamplePlan,
} from '../utils/api';
import { FIXTURE_SCENARIOS } from '../fixtures';

describe('API Client utilities (Task T9 / PR #15 fixes)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  describe('checkBackendHealth', () => {
    it('returns true when endpoint responds with 200 OK and application/json healthy', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'content-type': 'application/json' }),
        json: async () => ({ status: 'healthy' }),
      } as Response);

      const isHealthy = await checkBackendHealth();
      expect(isHealthy).toBe(true);
    });

    it('returns false when endpoint returns HTML SPA fallback instead of JSON', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        headers: new Headers({ 'content-type': 'text/html; charset=utf-8' }),
        json: async () => ({ status: 'healthy' }),
      } as Response);

      const isHealthy = await checkBackendHealth();
      expect(isHealthy).toBe(false);
    });

    it('returns false when endpoint returns non-ok status or network error', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: false,
        headers: new Headers({ 'content-type': 'application/json' }),
      } as Response);

      expect(await checkBackendHealth()).toBe(false);

      vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Network error'));
      expect(await checkBackendHealth()).toBe(false);
    });
  });

  describe('fetchAdminFaults and setDepartmentFault', () => {
    it('fetches admin fault status', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Healthy' },
          fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Healthy' },
          water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Healthy' },
          structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Healthy' },
        }),
      } as Response);

      const res = await fetchAdminFaults();
      expect(res.fire.mode).toBe('none');
    });

    it('sets department fault mode', async () => {
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          department: 'fire',
          mode: 'offline',
          delay_ms: 3000,
          message: 'Fault injected',
        }),
      } as Response);

      const res = await setDepartmentFault({ department: 'fire', mode: 'offline', delay_ms: 3000 });
      expect(res.mode).toBe('offline');
      expect(fetchSpy).toHaveBeenCalledWith('/admin/fault', expect.objectContaining({ method: 'POST' }));
    });
  });

  describe('restoreAllDepartmentsHealthy', () => {
    it('restores all 4 departments in parallel using Promise.all', async () => {
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, opts: any) => {
        const body = JSON.parse(opts.body);
        return {
          ok: true,
          json: async () => ({
            department: body.department,
            mode: 'none',
            delay_ms: 0,
            message: `Department ${body.department} restored`,
          }),
        } as Response;
      });

      const results = await restoreAllDepartmentsHealthy();

      expect(fetchSpy).toHaveBeenCalledTimes(4);
      expect(Object.keys(results)).toEqual(['zoning', 'fire', 'water', 'structural']);
      expect(results.zoning.mode).toBe('none');
      expect(results.fire.mode).toBe('none');
      expect(results.water.mode).toBe('none');
      expect(results.structural.mode).toBe('none');
    });
  });

  describe('submitPermit and getPermitStatus', () => {
    it('submits a permit and returns SubmitPermitResponse', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          permit_id: 'pm-123',
          trace_id: 'trc-123',
          state: 'RECEIVED',
          stream_url: '/permits/pm-123/stream',
        }),
      } as Response);

      const samplePermit = FIXTURE_SCENARIOS.approved.permit;

      const res = await submitPermit(samplePermit);
      expect(res.permit_id).toBe('pm-123');
      expect(res.stream_url).toBe('/permits/pm-123/stream');
    });

    it('fetches permit status', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          permit_id: 'pm-123',
          state: 'DECIDED',
          final_decision: 'APPROVED',
          decision_code: 'ALL_APPROVED',
          reviews: {},
          events: [],
        }),
      } as Response);

      const res = await getPermitStatus('pm-123');
      expect(res.final_decision).toBe('APPROVED');
    });
  });

  describe('Intake API endpoints (Task T13)', () => {
    it('posts to /intake/parse and receives parsed permit payload', async () => {
      const samplePermit = FIXTURE_SCENARIOS.approved.permit;
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          permit: samplePermit,
          filename: 'plan.pdf',
          file_path: 'uploads/pm-intake-1_plan.pdf',
          extracted_by: 'gemini-2.5-flash',
          warnings: [],
        }),
      } as Response);

      const res = await parseIntakePlan({
        filename: 'plan.pdf',
        content_base64: 'JVBER...',
        mime_type: 'application/pdf',
      });

      expect(fetchSpy).toHaveBeenCalledWith('/intake/parse', expect.objectContaining({ method: 'POST' }));
      expect(res.extracted_by).toBe('gemini-2.5-flash');
      expect(res.permit.project_title).toBe(samplePermit.project_title);
    });

    it('fetches sample plans catalog and individual plan detail', async () => {
      vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce({
          ok: true,
          json: async () => [
            { id: 'sample1', title: 'Sample 1', filename: 's1.txt', description: 'Desc', mime_type: 'text/plain' },
          ],
        } as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            id: 'sample1',
            title: 'Sample 1',
            filename: 's1.txt',
            description: 'Desc',
            mime_type: 'text/plain',
            sample_text: 'Plan text',
          }),
        } as Response);

      const catalog = await fetchSamplePlans();
      expect(catalog.length).toBe(1);

      const detail = await fetchSamplePlan('sample1');
      expect(detail.sample_text).toBe('Plan text');
    });
  });
});
