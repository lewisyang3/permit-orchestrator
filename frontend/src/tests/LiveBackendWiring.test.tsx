import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import App from '../App';
import * as api from '../utils/api';
import { FIXTURE_SCENARIOS } from '../fixtures';

describe('Live Backend Wiring & SSE Streaming (Task T9)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('switches to Live Orchestrator mode and renders live control status', async () => {
    vi.spyOn(api, 'checkBackendHealth').mockResolvedValue(true);
    vi.spyOn(api, 'fetchAdminFaults').mockResolvedValue({
      zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Healthy' },
      fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Healthy' },
      water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Healthy' },
      structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Healthy' },
    });

    render(<App />);

    // Click Live Orchestrator mode button in header
    const liveModeButton = screen.getByText('Live Orchestrator (SSE)');
    fireEvent.click(liveModeButton);

    expect(screen.getByText('Live Orchestrator SSE Mode Active')).toBeInTheDocument();
    expect(screen.getByText('Submit to Orchestrator')).toBeInTheDocument();
  });

  it('submits permit in Live mode, receives live SSE stream, and updates trace waterfall', async () => {
    vi.spyOn(api, 'checkBackendHealth').mockResolvedValue(true);
    const submitSpy = vi.spyOn(api, 'submitPermit').mockResolvedValue({
      permit_id: 'pm-live-test-001',
      trace_id: 'trc-live-test-001',
      state: 'RECEIVED',
      stream_url: '/permits/pm-live-test-001/stream',
    });

    render(<App />);

    // Switch to live mode
    fireEvent.click(screen.getByText('Live Orchestrator (SSE)'));

    // Wait for backend to be recognized as online
    await waitFor(() => expect(screen.getByText('Backend :8000 Online')).toBeInTheDocument());

    // Click Submit to Orchestrator
    const submitButton = screen.getByText('Submit to Orchestrator');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(submitSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          permit_id: expect.stringMatching(/^pm-[a-z0-9]+-[a-z0-9]+$/),
        })
      );
      // Ensure it does not send the static fixture ID
      expect(submitSpy).not.toHaveBeenCalledWith(
        expect.objectContaining({
          permit_id: 'pm-fixture-approved-001',
        })
      );
      expect(screen.getByText(/Permit: pm-live-test-001/i)).toBeInTheDocument();
    });

    // The EventSource mock instance should have been instantiated
    const mockES = (globalThis as any).EventSource.instances.at(-1);
    expect(mockES).toBeDefined();
    expect(mockES.url).toBe('/permits/pm-live-test-001/stream');

    // Simulate streaming events from backend
    const timeoutEvents = FIXTURE_SCENARIOS.timeout.events;

    act(() => {
      // Emit PERMIT_RECEIVED and SPAN_STARTED events
      mockES.emit('trace_event', timeoutEvents[0]);
      mockES.emit('trace_event', timeoutEvents[1]);
      mockES.emit('trace_event', timeoutEvents[2]);
    });

    await waitFor(() => {
      expect(screen.getByText('Suburban Storage Warehouse Modernization')).toBeInTheDocument();
    });

    // Emit all remaining events through PERMIT_DECIDED
    act(() => {
      for (let i = 3; i < timeoutEvents.length; i++) {
        mockES.emit('trace_event', timeoutEvents[i]);
      }
      mockES.emit('done', { permit_id: 'pm-live-test-001', final_decision: 'REJECTED' });
    });

    // Verify visible timeout, retry markers, and consensus refusal
    await waitFor(() => {
      expect(screen.getByText('INCOMPLETE_REVIEWS')).toBeInTheDocument();
      expect(screen.getByText(/Fail-Safe Consensus Guarantee/i)).toBeInTheDocument();
      expect(screen.getByText('3x')).toBeInTheDocument(); // Retry count marker on Fire span
      expect(screen.getByText('SAGA DECIDED')).toBeInTheDocument();
    });
  });

  it('disconnects active EventSource connection when switching from Live to Fixture mode', async () => {
    vi.spyOn(api, 'checkBackendHealth').mockResolvedValue(true);
    vi.spyOn(api, 'submitPermit').mockResolvedValue({
      permit_id: 'pm-mode-switch-001',
      trace_id: 'trc-mode-switch-001',
      state: 'RECEIVED',
      stream_url: '/permits/pm-mode-switch-001/stream',
    });

    render(<App />);

    // Switch to Live mode
    fireEvent.click(screen.getByText('Live Orchestrator (SSE)'));
    await waitFor(() => expect(screen.getByText('Backend :8000 Online')).toBeInTheDocument());

    // Submit permit to establish SSE connection
    fireEvent.click(screen.getByText('Submit to Orchestrator'));

    await waitFor(() => {
      expect(screen.getByText(/Permit: pm-mode-switch-001/i)).toBeInTheDocument();
    });

    const activeES = (globalThis as any).EventSource.instances.at(-1);
    expect(activeES).toBeDefined();
    const closeSpy = vi.spyOn(activeES, 'close');

    // Switch back to Golden Fixtures mode
    fireEvent.click(screen.getByText('Golden Fixtures'));

    // Verify EventSource is closed and live streaming state is terminated
    expect(closeSpy).toHaveBeenCalled();
    expect(screen.queryByText('Live Orchestrator SSE Mode Active')).not.toBeInTheDocument();
  });
});
