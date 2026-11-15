import React, { useState, useEffect, useRef } from 'react';
import { Header } from './components/Header';
import { FixtureSelector } from './components/FixtureSelector';
import { ConsensusBanner } from './components/ConsensusBanner';
import { TraceWaterfall } from './components/TraceWaterfall';
import { SpanDetailDrawer } from './components/SpanDetailDrawer';
import { PermitForm } from './components/PermitForm';
import { FIXTURE_SCENARIOS, FixtureScenario } from './fixtures';
import { parseTraceEvents, ParsedSpan } from './utils/traceParser';
import { TraceEvent, Permit } from './types';
import { Activity, RotateCcw, AlertCircle } from 'lucide-react';

export const App: React.FC = () => {
  const [mode, setMode] = useState<'fixture' | 'live'>('fixture');
  const [selectedScenario, setSelectedScenario] = useState<FixtureScenario>(FIXTURE_SCENARIOS.approved);
  const [currentPermit, setCurrentPermit] = useState<Permit>(FIXTURE_SCENARIOS.approved.permit);
  const [activeEvents, setActiveEvents] = useState<TraceEvent[]>(FIXTURE_SCENARIOS.approved.events);
  const [selectedSpan, setSelectedSpan] = useState<ParsedSpan | null>(null);

  // Simulation & Replay state
  const [isSimulating, setIsSimulating] = useState(false);
  const simulationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Live backend state (for T9 forward-compatibility)
  const [livePermitId, setLivePermitId] = useState<string | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [isSubmittingLive, setIsSubmittingLive] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Parse events into distributed trace model
  const parsedTrace = parseTraceEvents(activeEvents);

  // Update selected span reference if parsed spans change
  useEffect(() => {
    if (selectedSpan) {
      const updated = parsedTrace.spans.find((s) => s.id === selectedSpan.id);
      if (updated) setSelectedSpan(updated);
    }
  }, [activeEvents]);

  // Handle fixture selection
  const handleSelectScenario = (scenario: FixtureScenario) => {
    if (simulationTimerRef.current) {
      clearInterval(simulationTimerRef.current);
      simulationTimerRef.current = null;
    }
    setIsSimulating(false);
    setSelectedScenario(scenario);
    setCurrentPermit(scenario.permit);
    setActiveEvents(scenario.events);
  };

  // Simulate replay of fixture events stream
  const handleSimulateReplay = (scenario: FixtureScenario) => {
    if (simulationTimerRef.current) {
      clearInterval(simulationTimerRef.current);
    }

    setSelectedScenario(scenario);
    setCurrentPermit(scenario.permit);
    setActiveEvents([]);
    setIsSimulating(true);

    const allEvents = scenario.events;
    let idx = 0;

    simulationTimerRef.current = setInterval(() => {
      if (idx < allEvents.length) {
        const nextEvt = allEvents[idx];
        setActiveEvents((prev) => [...prev, nextEvt]);
        idx++;
      } else {
        if (simulationTimerRef.current) {
          clearInterval(simulationTimerRef.current);
          simulationTimerRef.current = null;
        }
        setIsSimulating(false);
      }
    }, 120); // 120ms tick per event
  };

  // Clean up timers & event sources
  useEffect(() => {
    return () => {
      if (simulationTimerRef.current) clearInterval(simulationTimerRef.current);
      if (eventSourceRef.current) eventSourceRef.current.close();
    };
  }, []);

  // Live submit handler (forward-compatible for T9)
  const handleSubmitLive = async () => {
    setLiveError(null);
    setIsSubmittingLive(true);
    setActiveEvents([]);

    try {
      const res = await fetch('/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentPermit),
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`Submit failed with HTTP ${res.status}: ${errText}`);
      }

      const data = await res.json();
      setLivePermitId(data.permit_id);

      // Connect to SSE stream
      const streamUrl = data.stream_url || `/permits/${data.permit_id}/stream`;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }

      const es = new EventSource(streamUrl);
      eventSourceRef.current = es;

      es.addEventListener('trace_event', (e) => {
        try {
          const parsed: TraceEvent = JSON.parse(e.data);
          setActiveEvents((prev) => [...prev, parsed]);
        } catch (err) {
          console.error('Failed to parse SSE trace_event:', err);
        }
      });

      es.addEventListener('done', (_e) => {
        es.close();
        setIsSubmittingLive(false);
      });

      es.onerror = (err) => {
        console.warn('SSE stream closed or encountered error:', err);
        es.close();
        setIsSubmittingLive(false);
      };
    } catch (err: any) {
      setLiveError(err.message || 'Failed to submit permit to live orchestrator');
      setIsSubmittingLive(false);
    }
  };

  const handleSubmit = () => {
    if (mode === 'live') {
      handleSubmitLive();
    } else {
      handleSimulateReplay(selectedScenario);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col antialiased">
      <Header mode={mode} onModeChange={setMode} />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-6 space-y-6">
        {/* Live mode banner / alerts */}
        {mode === 'live' && (
          <div className="p-4 rounded-xl bg-sky-950/40 border border-sky-500/40 text-xs text-sky-200 flex items-start justify-between gap-4">
            <div className="flex items-start space-x-2.5">
              <Activity className="h-4 w-4 text-sky-400 mt-0.5 shrink-0" />
              <div>
                <p className="font-semibold text-sky-100 mb-0.5">
                  Live Orchestrator SSE Mode Active
                </p>
                <p className="text-sky-300/80">
                  Submissions post directly to <code className="font-mono bg-sky-900/60 px-1 py-0.5 rounded">POST http://localhost:8000/submit</code> and stream live saga events over Server-Sent Events.
                </p>
              </div>
            </div>
            {livePermitId && (
              <span className="font-mono bg-sky-900/60 px-2 py-1 rounded text-sky-200 shrink-0">
                Active ID: {livePermitId}
              </span>
            )}
          </div>
        )}

        {liveError && (
          <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200 flex items-center space-x-2">
            <AlertCircle className="h-4 w-4 text-rose-400 shrink-0" />
            <span>{liveError}</span>
          </div>
        )}

        {/* Fixture Scenario Selector */}
        {mode === 'fixture' && (
          <FixtureSelector
            selectedId={selectedScenario.id}
            onSelect={handleSelectScenario}
            onSimulateReplay={handleSimulateReplay}
            isSimulating={isSimulating}
            onViewPermitJson={handleSelectScenario}
          />
        )}

        {/* Consensus Result Banner */}
        <ConsensusBanner trace={parsedTrace} />

        {/* Distributed Trace Waterfall View */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-200 flex items-center space-x-2">
              <span>Distributed Trace Waterfall</span>
              {isSimulating && (
                <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-mono bg-sky-500/20 text-sky-300 border border-sky-500/40 animate-pulse">
                  <Activity className="h-3 w-3" />
                  <span>Streaming Events...</span>
                </span>
              )}
            </h2>
            <div className="flex items-center space-x-2 text-xs">
              <button
                type="button"
                onClick={() => handleSimulateReplay(selectedScenario)}
                disabled={isSimulating}
                className="px-3 py-1 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 flex items-center space-x-1.5 transition-colors disabled:opacity-50"
              >
                <RotateCcw className="h-3 w-3" />
                <span>Replay Trace</span>
              </button>
            </div>
          </div>

          <TraceWaterfall
            trace={parsedTrace}
            selectedSpan={selectedSpan}
            onSelectSpan={(span) => setSelectedSpan(span)}
          />
        </div>

        {/* Permit Submission Payload & Form */}
        <PermitForm
          permit={currentPermit}
          onChange={setCurrentPermit}
          onSubmit={handleSubmit}
          isSubmitting={isSimulating || isSubmittingLive}
          mode={mode}
        />
      </main>

      {/* Side Drawer for Span Inspection */}
      <SpanDetailDrawer span={selectedSpan} onClose={() => setSelectedSpan(null)} />
    </div>
  );
};

export default App;
