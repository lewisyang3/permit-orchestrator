import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { PermitForm } from '../components/PermitForm';
import { EvidenceProvider } from '../components/EvidenceContext';
import { SpanDetailDrawer } from '../components/SpanDetailDrawer';
import { FIXTURE_SCENARIOS } from '../fixtures';
import { parseTraceEvents } from '../utils/traceParser';
import { evidenceTone, summarizeEvidence } from '../utils/evidence';
import * as api from '../utils/api';
import { EvidenceSpan, Permit, TraceEvent } from '../types';

const span = (path: string, over: Partial<EvidenceSpan> = {}): EvidenceSpan => ({
  field_path: path,
  quote: 'Front Setback: 25.0 ft',
  page: 1,
  line: 21,
  bboxes: [{ x0: 40, top: 280, x1: 140, bottom: 289 }],
  status: 'verified',
  value_supported: true,
  method: 'deterministic',
  ...over,
});

const permitWithEvidence = (): Permit => ({
  ...FIXTURE_SCENARIOS.approved.permit,
  source_document_id: null,
  evidence: {
    'zoning.setback_front_ft': span('zoning.setback_front_ft'),
    'zoning.setback_rear_ft': span('zoning.setback_rear_ft', {
      quote: 'Rear Setback: 12.0 ft',
      status: 'unverified',
      bboxes: [],
      method: 'gemini',
    }),
    'zoning.setback_side_ft': span('zoning.setback_side_ft', { quote: 'Rear Setback: 32.0 ft', value_supported: false }),
  },
});

afterEach(() => vi.restoreAllMocks());

describe('evidence helpers', () => {
  it('ranks trust from the server-side check', () => {
    expect(evidenceTone(span('a'))).toBe('verified');
    expect(evidenceTone(span('a', { value_supported: false }))).toBe('mismatch');
    expect(evidenceTone(span('a', { status: 'unverified' }))).toBe('unverified');
    expect(evidenceTone(undefined)).toBe('none');
    expect(summarizeEvidence(permitWithEvidence())).toEqual({ total: 3, verified: 1, mismatch: 1, unverified: 1 });
    expect(summarizeEvidence(FIXTURE_SCENARIOS.approved.permit)).toBeNull();
  });
});

describe('PermitForm evidence chips', () => {
  const renderForm = (permit: Permit) => {
    vi.spyOn(api, 'fetchSamplePlans').mockResolvedValue([]);
    return render(
      <EvidenceProvider documentId={permit.source_document_id}>
        <PermitForm permit={permit} onChange={vi.fn()} onSubmit={vi.fn()} isSubmitting={false} mode="live" />
      </EvidenceProvider>,
    );
  };

  it('shows a page badge per cited field, marked by trust', () => {
    renderForm(permitWithEvidence());
    expect(screen.getByTestId('evidence-zoning.setback_front_ft')).toHaveTextContent('p.1');
    expect(screen.getByTestId('evidence-zoning.setback_rear_ft')).toHaveTextContent('p.1 ?');
    expect(screen.getByTestId('evidence-zoning.setback_side_ft')).toHaveTextContent('p.1 ≠');
    // Fields without a citation get no badge.
    expect(screen.queryByTestId('evidence-zoning.zone_code')).toBeNull();
  });

  it('fixture permits without evidence render no badges', () => {
    renderForm(FIXTURE_SCENARIOS.approved.permit);
    expect(screen.queryAllByTestId(/^evidence-/)).toHaveLength(0);
  });

  it('clicking a badge opens the source viewer with the quote', () => {
    renderForm(permitWithEvidence());
    fireEvent.click(screen.getByTestId('evidence-zoning.setback_rear_ft'));
    const dialog = screen.getByRole('dialog', { name: 'Source for zoning.setback_rear_ft' });
    expect(within(dialog).getByText(/Rear Setback: 12.0 ft/)).toBeInTheDocument();
    expect(within(dialog).getByText('Not found in the document; check by hand')).toBeInTheDocument();
    // No upload to show (fixture/sample): say so instead of a blank page.
    expect(within(dialog).getByText(/original document isn't available/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Close source viewer'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('text uploads open at the cited line, highlighted', async () => {
    const text = Array.from({ length: 25 }, (_, i) => (i === 20 ? 'Front Setback: 25.0 ft' : `line ${i + 1}`)).join('\n');
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(text, { status: 200 }));
    const permit: Permit = {
      ...permitWithEvidence(),
      source_document_id: 'pm-intake-0123abcd',
      evidence: { 'zoning.setback_front_ft': span('zoning.setback_front_ft', { page: null, bboxes: [] }) },
    };
    vi.spyOn(api, 'fetchSamplePlans').mockResolvedValue([]);
    render(
      <EvidenceProvider documentId={permit.source_document_id}>
        <PermitForm permit={permit} onChange={vi.fn()} onSubmit={vi.fn()} isSubmitting={false} mode="live" />
      </EvidenceProvider>,
    );
    fireEvent.click(screen.getByTestId('evidence-zoning.setback_front_ft'));
    const hit = await screen.findByTestId('evidence-highlight');
    expect(hit).toHaveTextContent('21Front Setback: 25.0 ft');
    expect(fetchMock).toHaveBeenCalledWith('/intake/documents/pm-intake-0123abcd');
  });
});

describe('department findings', () => {
  const finding = {
    department: 'zoning',
    rule_id: 'SJ-Z-101',
    rule_name: 'Front setback',
    passed: false,
    severity: 'VIOLATION',
    message: 'Front setback 18 ft is below the 20 ft minimum.',
    evidence: [span('zoning.setback_front_ft', { quote: "FRONT SETBACK: 18'-0\"" })],
    rule_sources: [{ url: 'https://example.org/guide.pdf', section: 'SJMC 20.30.200, Table 20-60', printed_page: 19 }],
  };
  const events: TraceEvent[] = [
    { event_id: 'e1', trace_id: 't', permit_id: 'p', span: 'dept:zoning', parent_span: 'orchestrator', type: 'SPAN_STARTED', ts: '2026-11-23T10:00:00.000Z', detail: {} },
    {
      event_id: 'e2', trace_id: 't', permit_id: 'p', span: 'dept:zoning', parent_span: 'orchestrator', type: 'CALL_SUCCEEDED',
      ts: '2026-11-23T10:00:00.200Z',
      detail: { status: 'REJECTED', attempt: 1, latency_ms: 200, jurisdiction_id: 'ca-san-jose', findings: [finding] },
    },
  ] as unknown as TraceEvent[];

  it('trace parser keeps findings and the jurisdiction per department', () => {
    const zoning = parseTraceEvents(events).spans.find((s) => s.id === 'dept:zoning')!;
    expect(zoning.jurisdictionId).toBe('ca-san-jose');
    expect(zoning.findings).toHaveLength(1);
    expect(zoning.findings[0].evidence?.[0].quote).toBe("FRONT SETBACK: 18'-0\"");
  });

  it('drawer shows what the plan says and the city rule for each finding', async () => {
    const zoning = parseTraceEvents(events).spans.find((s) => s.id === 'dept:zoning')!;
    render(
      <EvidenceProvider documentId={null}>
        <SpanDetailDrawer span={zoning} onClose={vi.fn()} />
      </EvidenceProvider>,
    );
    expect(screen.getByText(/rules: ca-san-jose/)).toBeInTheDocument();
    expect(screen.getByText('Rule Findings (1)')).toBeInTheDocument();
    expect(screen.getByText('“FRONT SETBACK: 18\'-0"”')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /SJMC 20.30.200, Table 20-60, printed p.19/ });
    expect(link).toHaveAttribute('href', 'https://example.org/guide.pdf');
    fireEvent.click(screen.getByTestId('evidence-zoning.setback_front_ft'));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
  });
});
