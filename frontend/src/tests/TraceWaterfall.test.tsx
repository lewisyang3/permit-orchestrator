import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import App from '../App';

describe('Trace UI Frontend with Golden Fixtures (T7)', () => {
  it('renders application header and default approved fixture', () => {
    render(<App />);

    expect(screen.getByText('Permit Orchestrator')).toBeInTheDocument();
    expect(screen.getByText('Consensus Fail-Safe Enforced')).toBeInTheDocument();

    // Default fixture is approved
    expect(screen.getAllByText('APPROVED').length).toBeGreaterThan(0);
    expect(screen.getByText('ALL_DEPARTMENTS_APPROVED')).toBeInTheDocument();

    // Spans are rendered
    expect(screen.getAllByText('Orchestrator Saga').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Fire Department').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Consensus Gate').length).toBeGreaterThan(0);
  });

  it('switches to rejected fixture and displays violations', () => {
    render(<App />);

    // Click on rejected fixture card
    const rejectedCard = screen.getByText('Department Rejection (Fire Violations)');
    fireEvent.click(rejectedCard);

    expect(screen.getByText('DEPARTMENT_REJECTIONS')).toBeInTheDocument();
    expect(screen.getByText(/Commercial Restaurant Outdoor Dining/i)).toBeInTheDocument();
    expect(screen.getByText(/Violations Cited \(2\)/i)).toBeInTheDocument();
  });

  it('switches to timeout fixture and displays fail-safe retry markers', () => {
    render(<App />);

    // Click on timeout fixture card
    const timeoutCard = screen.getByText('Fail-Safe Timeout / Retry (Fire Offline)');
    fireEvent.click(timeoutCard);

    expect(screen.getByText('INCOMPLETE_REVIEWS')).toBeInTheDocument();
    expect(screen.getByText(/Fail-Safe Consensus Guarantee/i)).toBeInTheDocument();
    expect(screen.getByText(/Department \[fire\] timed out after 3 attempts/i)).toBeInTheDocument();
    expect(screen.getByText('3x')).toBeInTheDocument();
  });

  it('opens span detail drawer when clicking a span', () => {
    render(<App />);

    // Select timeout scenario
    const timeoutCard = screen.getByText('Fail-Safe Timeout / Retry (Fire Offline)');
    fireEvent.click(timeoutCard);

    // Click fire department span
    const fireSpanLabel = screen.getByText('dept:fire');
    fireEvent.click(fireSpanLabel);

    // Side drawer should open
    expect(screen.getByText('span: dept:fire')).toBeInTheDocument();
    expect(screen.getByText(/Retry Execution Flow/i)).toBeInTheDocument();
    expect(screen.getByText('Attempt #1')).toBeInTheDocument();
    expect(screen.getByText('Attempt #2')).toBeInTheDocument();
    expect(screen.getByText('Attempt #3')).toBeInTheDocument();
    expect(screen.getByText(/Span Events Timeline/i)).toBeInTheDocument();
  });
});
