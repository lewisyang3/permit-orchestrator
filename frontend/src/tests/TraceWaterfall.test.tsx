import React, { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import App from '../App';
import { ConsensusBanner } from '../components/ConsensusBanner';
import { TraceWaterfall } from '../components/TraceWaterfall';
import { SpanDetailDrawer } from '../components/SpanDetailDrawer';
import { FIXTURE_SCENARIOS } from '../fixtures';
import { parseTraceEvents, ParsedSpan } from '../utils/traceParser';
import { TraceEvent } from '../types';

/**
 * The rejected and timeout golden traces are no longer selectable from the app
 * (the Fixtures panel was removed), so render the trace components the way App
 * wires them, fed straight from the fixture events.
 */
const TraceHarness: React.FC<{ events: TraceEvent[] }> = ({ events }) => {
  const [selectedSpan, setSelectedSpan] = useState<ParsedSpan | null>(null);
  const trace = parseTraceEvents(events);
  return (
    <>
      <ConsensusBanner trace={trace} />
      <TraceWaterfall trace={trace} selectedSpan={selectedSpan} onSelectSpan={setSelectedSpan} />
      <SpanDetailDrawer span={selectedSpan} onClose={() => setSelectedSpan(null)} />
    </>
  );
};

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

  it('renders the rejected fixture trace with its violations', () => {
    render(<TraceHarness events={FIXTURE_SCENARIOS.rejected.events} />);

    expect(screen.getByText('DEPARTMENT_REJECTIONS')).toBeInTheDocument();
    expect(screen.getByText(/Violations Cited \(2\)/i)).toBeInTheDocument();
  });

  it('renders the timeout fixture trace with fail-safe retry markers', () => {
    render(<TraceHarness events={FIXTURE_SCENARIOS.timeout.events} />);

    expect(screen.getByText('INCOMPLETE_REVIEWS')).toBeInTheDocument();
    expect(screen.getByText(/Fail-Safe Consensus Guarantee/i)).toBeInTheDocument();
    expect(screen.getByText(/Department \[fire\] timed out after 3 attempts/i)).toBeInTheDocument();
    expect(screen.getByText('3x')).toBeInTheDocument();
  });

  it('opens span detail drawer when clicking a span', () => {
    render(<TraceHarness events={FIXTURE_SCENARIOS.timeout.events} />);

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

  it('no longer offers the removed Fixtures and Chaos Diagnostics panels', () => {
    render(<App />);

    expect(screen.queryByText('Fixtures')).not.toBeInTheDocument();
    expect(screen.queryByText('Chaos Diagnostics')).not.toBeInTheDocument();
    expect(screen.queryByText('Fault Injection & Chaos Controls')).not.toBeInTheDocument();
    expect(screen.queryByText('Kill Fire Department (Outage Demo)')).not.toBeInTheDocument();
  });
});
