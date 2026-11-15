import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FaultControlPanel } from '../components/FaultControlPanel';
import * as api from '../utils/api';

describe('FaultControlPanel Component (Task T9)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders all 4 departments and loads initial healthy status', async () => {
    vi.spyOn(api, 'fetchAdminFaults').mockResolvedValue({
      zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Zoning operating normally' },
      fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Fire operating normally' },
      water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Water operating normally' },
      structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Structural operating normally' },
    });

    render(<FaultControlPanel isBackendOnline={true} />);

    expect(screen.getByText('Fault Injection & Chaos Controls')).toBeInTheDocument();
    expect(screen.getByText('Fire Department')).toBeInTheDocument();
    expect(screen.getByText('Zoning Department')).toBeInTheDocument();
    expect(screen.getByText('Water Department')).toBeInTheDocument();
    expect(screen.getByText('Structural Department')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText('HEALTHY').length).toBe(4);
    });
  });

  it('handles clicking Offline (503) for Fire department', async () => {
    vi.spyOn(api, 'fetchAdminFaults').mockResolvedValue({
      zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Zoning operating normally' },
      fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Fire operating normally' },
      water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Water operating normally' },
      structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Structural operating normally' },
    });

    const setFaultSpy = vi.spyOn(api, 'setDepartmentFault').mockResolvedValue({
      department: 'fire',
      mode: 'offline',
      delay_ms: 3000,
      message: 'Fault mode for fire set to offline',
    });

    render(<FaultControlPanel isBackendOnline={true} />);

    await waitFor(() => {
      expect(screen.getAllByText('HEALTHY').length).toBe(4);
    });

    // Find the Offline (503) button inside the Fire card
    const offlineButtons = screen.getAllByText('Offline (503)');
    fireEvent.click(offlineButtons[0]); // Fire is first in DEPARTMENTS array

    await waitFor(() => {
      expect(setFaultSpy).toHaveBeenCalledWith({
        department: 'fire',
        mode: 'offline',
        delay_ms: 3000,
      });
      expect(screen.getByText('OFFLINE (503)')).toBeInTheDocument();
    });
  });

  it('triggers Kill Fire Department outage demo when button is clicked', async () => {
    vi.spyOn(api, 'fetchAdminFaults').mockResolvedValue({
      zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Zoning operating normally' },
      fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Fire operating normally' },
      water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Water operating normally' },
      structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Structural operating normally' },
    });

    const onRunFaultDemo = vi.fn();
    const setFaultSpy = vi.spyOn(api, 'setDepartmentFault').mockResolvedValue({
      department: 'fire',
      mode: 'offline',
      delay_ms: 3000,
      message: 'Fault mode for fire set to offline',
    });

    render(<FaultControlPanel isBackendOnline={true} onRunFaultDemo={onRunFaultDemo} />);

    const killButton = screen.getByText('Kill Fire Department (Outage Demo)');
    await waitFor(() => expect(killButton).not.toBeDisabled());
    fireEvent.click(killButton);

    await waitFor(() => {
      expect(setFaultSpy).toHaveBeenCalledWith({
        department: 'fire',
        mode: 'offline',
        delay_ms: 3000,
      });
      expect(onRunFaultDemo).toHaveBeenCalled();
    });
  });

  it('restores all departments when Restore All button is clicked', async () => {
    vi.spyOn(api, 'fetchAdminFaults').mockResolvedValue({
      zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Zoning operating normally' },
      fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Fire operating normally' },
      water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Water operating normally' },
      structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Structural operating normally' },
    });

    const restoreSpy = vi.spyOn(api, 'restoreAllDepartmentsHealthy').mockResolvedValue({
      zoning: { department: 'zoning', mode: 'none', delay_ms: 0, message: 'Zoning operating normally' },
      fire: { department: 'fire', mode: 'none', delay_ms: 0, message: 'Fire operating normally' },
      water: { department: 'water', mode: 'none', delay_ms: 0, message: 'Water operating normally' },
      structural: { department: 'structural', mode: 'none', delay_ms: 0, message: 'Structural operating normally' },
    });

    render(<FaultControlPanel isBackendOnline={true} />);

    const restoreButton = screen.getByText('Restore All');
    await waitFor(() => expect(restoreButton).not.toBeDisabled());
    fireEvent.click(restoreButton);

    await waitFor(() => {
      expect(restoreSpy).toHaveBeenCalled();
      expect(screen.getByText('All 4 departments restored to HEALTHY (mode: none)')).toBeInTheDocument();
    });
  });
});
