import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PermitForm } from '../components/PermitForm';
import * as api from '../utils/api';
import { FIXTURE_SCENARIOS } from '../fixtures';

describe('Intake Plan & PDF Parsing Component (Task T13)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const samplePermit = FIXTURE_SCENARIOS.approved.permit;

  it('renders intake upload controls and sample plan dropdown', async () => {
    vi.spyOn(api, 'fetchSamplePlans').mockResolvedValue([
      {
        id: 'residential_addition',
        title: 'Residential Rear Addition',
        filename: 'residential_rear_addition_plan.txt',
        description: 'Single family residential addition',
        mime_type: 'text/plain',
      },
    ]);

    render(
      <PermitForm
        permit={samplePermit}
        onChange={vi.fn()}
        onSubmit={vi.fn()}
        isSubmitting={false}
        mode="live"
      />
    );

    expect(screen.getByText('Upload Plan / PDF')).toBeInTheDocument();
    expect(screen.getByText('Load Sample Plan...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Residential Rear Addition')).toBeInTheDocument();
    });
  });

  it('loads a sample blueprint and updates the permit form', async () => {
    vi.spyOn(api, 'fetchSamplePlans').mockResolvedValue([
      {
        id: 'commercial_restaurant',
        title: 'Commercial Restaurant Patio',
        filename: 'commercial_restaurant_plan.txt',
        description: 'Commercial restaurant expansion',
        mime_type: 'text/plain',
      },
    ]);

    vi.spyOn(api, 'fetchSamplePlan').mockResolvedValue({
      id: 'commercial_restaurant',
      title: 'Commercial Restaurant Patio',
      filename: 'commercial_restaurant_plan.txt',
      description: 'Commercial restaurant expansion',
      mime_type: 'text/plain',
      sample_text: 'Project Title: Commercial Restaurant Outdoor Dining Expansion',
    });

    const parsedPermit = {
      ...samplePermit,
      project_title: 'Commercial Restaurant Outdoor Dining Expansion',
      estimated_cost_usd: 210000,
    };

    const parseIntakeSpy = vi.spyOn(api, 'parseIntakePlan').mockResolvedValue({
      permit: parsedPermit,
      filename: 'commercial_restaurant_plan.txt',
      file_path: 'uploads/pm-intake-test_commercial.txt',
      extracted_by: 'gemini-2.5-flash',
      warnings: [],
    });

    const onChangeMock = vi.fn();

    render(
      <PermitForm
        permit={samplePermit}
        onChange={onChangeMock}
        onSubmit={vi.fn()}
        isSubmitting={false}
        mode="live"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Commercial Restaurant Patio')).toBeInTheDocument();
    });

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'commercial_restaurant' } });

    await waitFor(() => {
      expect(parseIntakeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          filename: 'commercial_restaurant_plan.txt',
          content_text: 'Project Title: Commercial Restaurant Outdoor Dining Expansion',
        })
      );
      expect(onChangeMock).toHaveBeenCalledWith(parsedPermit);
      expect(screen.getByText('Plan Parsed Successfully')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
      expect(screen.getByText('Source: commercial_restaurant_plan.txt')).toBeInTheDocument();
    });
  });

  it('handles plan file upload and calls parseIntakePlan with base64 for PDF', async () => {
    vi.spyOn(api, 'fetchSamplePlans').mockResolvedValue([]);

    const parsedPermit = {
      ...samplePermit,
      project_title: 'Uploaded Blueprint Building Permit',
    };

    const parseIntakeSpy = vi.spyOn(api, 'parseIntakePlan').mockResolvedValue({
      permit: parsedPermit,
      filename: 'blueprint.pdf',
      file_path: 'uploads/pm-intake-123_blueprint.pdf',
      extracted_by: 'deterministic_fallback',
      warnings: ['Extracted via heuristic fallback'],
    });

    const onChangeMock = vi.fn();

    const { container } = render(
      <PermitForm
        permit={samplePermit}
        onChange={onChangeMock}
        onSubmit={vi.fn()}
        isSubmitting={false}
        mode="live"
      />
    );

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeInTheDocument();

    const dummyPdf = new File(['%PDF-dummy'], 'blueprint.pdf', { type: 'application/pdf' });

    // Mock FileReader readAsDataURL
    const originalFileReader = globalThis.FileReader;
    const mockFileReader: any = {
      readAsDataURL: vi.fn(function (this: any) {
        this.result = 'data:application/pdf;base64,JVBERHVtbXk=';
        this.onload?.();
      }),
      readAsText: vi.fn(),
    };
    (globalThis as any).FileReader = vi.fn(() => mockFileReader);

    fireEvent.change(fileInput, { target: { files: [dummyPdf] } });

    await waitFor(() => {
      expect(parseIntakeSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          filename: 'blueprint.pdf',
          content_base64: 'JVBERHVtbXk=',
          mime_type: 'application/pdf',
        })
      );
      expect(onChangeMock).toHaveBeenCalledWith(parsedPermit);
      expect(screen.getByText('deterministic_fallback')).toBeInTheDocument();
      expect(screen.getByText(/Extracted via heuristic fallback/i)).toBeInTheDocument();
    });

    globalThis.FileReader = originalFileReader;
  });

  it('displays error message when plan parsing fails', async () => {
    vi.spyOn(api, 'fetchSamplePlans').mockResolvedValue([
      {
        id: 'residential_addition',
        title: 'Residential Rear Addition',
        filename: 'residential_rear_addition_plan.txt',
        description: 'Single family residential addition',
        mime_type: 'text/plain',
      },
    ]);
    vi.spyOn(api, 'fetchSamplePlan').mockRejectedValue(new Error('Network error loading sample plan'));

    render(
      <PermitForm
        permit={samplePermit}
        onChange={vi.fn()}
        onSubmit={vi.fn()}
        isSubmitting={false}
        mode="live"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Residential Rear Addition')).toBeInTheDocument();
    });

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'residential_addition' } });

    await waitFor(() => {
      expect(screen.getByText('Network error loading sample plan')).toBeInTheDocument();
    });
  });
});
