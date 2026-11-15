import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Header } from './components/Header';
import { FixtureSelector } from './components/FixtureSelector';
import { ConsensusBanner } from './components/ConsensusBanner';
import { TraceWaterfall } from './components/TraceWaterfall';
import { SpanDetailDrawer } from './components/SpanDetailDrawer';
import { PermitForm } from './components/PermitForm';
import { FaultControlPanel } from './components/FaultControlPanel';
import { FIXTURE_SCENARIOS, FixtureScenario } from './fixtures';
import { parseTraceEvents, ParsedSpan } from './utils/traceParser';
import { TraceEvent, Permit } from './types';
import { checkBackendHealth, submitPermit } from './utils/api';
import { Activity, RotateCcw, AlertCircle, Radio, CheckCircle2 } from 'lucide-react';

export const App: React.FC = () => {
  const [mode, setMode] = useState<'fixture' | 'live'>('fixture');
  const [isBackendOnline, setIsBackendOnline] = useState(false);
  const [selectedScenario, setSelectedScenario] = useState<FixtureScenario>(FIXTURE_SCENARIOS.approved);
  const [currentPermit, setCurrentPermit] = useState<Permit>(FIXTURE_SCENARIOS.approved.permit);
  const [activeEvents, setActiveEvents] = useState<TraceEvent[]>(FIXTURE_SCENARIOS.approved.events);
  const [selectedSpan, setSelectedSpan] = useState<ParsedSpan | null>(null);

  // Simulation & Replay state
  const [isSimulating, setIsSimulating] = useState(false);
  const simulationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Live backend state (T9)
  const [livePermitId, setLivePermitId] = useState<string | null>(null);
  const [liveTraceId, setLiveTraceId] = useState<string | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [isSubmittingLive, setIsSubmittingLive] = useState(false);
  const [isStreamingLive, setIsStreamingLive] = useState(false);
  const [liveConnectionStatus, setLiveConnectionStatus] = useState<
    'idle' | 'submitting' | 'streaming' | 'completed' | 'error'
  >('idle');
  const eventSourceRef = useRef<EventSource | null>(null);
  const seenEventIdsRef = useRef<Set<string>>(new Set());

  // Backend health polling
  const checkHealth = useCallback(async () => {
    const online = await checkBackendHealth();
    setIsBackendOnline(online);
  }, []);

  useEffect(() => {
    checkHealth();
    // Only poll periodically outside tests
    if ((import.meta as any).env?.MODE !== 'test') {
      const interval = setInterval(checkHealth, 5000);
      return () => clearInterval(interval);
    }
  }, [checkHealth]);

  // Parse events into distributed trace model
  const parsedTrace = parseTraceEvents(activeEvents);

  // Update selected span reference when activeEvents change
  useEffect(() => {
    if (selectedSpan) {
      const updated = parsedTrace.spans.find((s) => s.id === selectedSpan.id);
      if (updated && updated !== selectedSpan) setSelectedSpan(updated);
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

  // Clean up timers & event sources on unmount
  useEffect(() => {
    return () => {
      if (simulationTimerRef.current) clearInterval(simulationTimerRef.current);
      if (eventSourceRef.current) eventSourceRef.current.close();
    };
  }, []);

  // Live submit handler (Task T9 live SSE wiring)
  const handleSubmitLive = async () => {
    setLiveError(null);
    setIsSubmittingLive(true);
    setLiveConnectionStatus('submitting');
    setActiveEvents([]);
    seenEventIdsRef.current.clear();

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    try {
      const permitToSubmit: Permit = {
        ...currentPermit,
        permit_id: `pm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      };
      const data = await submitPermit(permitToSubmit);
      setLivePermitId(data.permit_id);
      setLiveTraceId(data.trace_id);
      setIsSubmittingLive(false);
      setIsStreamingLive(true);
      setLiveConnectionStatus('streaming');

      // Connect to SSE stream
      const streamUrl = data.stream_url || `/permits/${data.permit_id}/stream`;
      const es = new EventSource(streamUrl);
      eventSourceRef.current = es;

      es.addEventListener('trace_event', (e) => {
        try {
          const parsed: TraceEvent = JSON.parse(e.data);
          if (parsed && parsed.event_id && !seenEventIdsRef.current.has(parsed.event_id)) {
            seenEventIdsRef.current.add(parsed.event_id);
            setActiveEvents((prev) => [...prev, parsed]);
          }
        } catch (err) {
          console.error('Failed to parse SSE trace_event:', err);
        }
      });

      es.addEventListener('done', (_e) => {
        es.close();
        eventSourceRef.current = null;
        setIsStreamingLive(false);
        setLiveConnectionStatus('completed');
      });

      es.onerror = (err) => {
        // In SSE, readyState === 2 means stream closed
        if (es.readyState === 2) {
          setIsStreamingLive(false);
          setLiveConnectionStatus((prev) => (prev === 'streaming' ? 'completed' : prev));
        } else {
          console.warn('SSE stream error or reconnecting:', err);
        }
      };
    } catch (err: any) {
      setLiveError(err.message || 'Failed to submit permit to live orchestrator');
      setIsSubmittingLive(false);
      setIsStreamingLive(false);
      setLiveConnectionStatus('error');
    }
  };

  const handleSubmit = () => {
    if (mode === 'live') {
      handleSubmitLive();
    } else {
      handleSimulateReplay(selectedScenario);
    }
  };

  // Mode switcher disconnecting active SSE stream when toggling back to fixture mode
  const handleModeChange = (newMode: 'fixture' | 'live') => {
    if (newMode === 'fixture' && eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
      setIsStreamingLive(false);
    }
    setMode(newMode);
  };

  // One-click live demo flow: trigger live submission after setting fault mode
  const handleRunFaultDemo = () => {
    setMode('live');
    // Short delay to allow fault endpoint to register before submitting
    setTimeout(() => {
      handleSubmitLive();
    }, 150);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col antialiased">
      <Header mode={mode} onModeChange={handleModeChange} isBackendOnline={isBackendOnline} />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-6 space-y-6">
        {/* Live mode banner / active stream status */}
        {mode === 'live' && (
          <div className="p-4 rounded-xl bg-sky-950/40 border border-sky-500/40 text-xs text-sky-200 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 shadow-sm">
            <div className="flex items-start space-x-3">
              <div className="p-2 rounded-lg bg-sky-500/15 border border-sky-500/30 text-sky-400 shrink-0">
                <Activity className="h-5 w-5" />
              </div>
              <div>
                <div className="flex items-center space-x-2">
                  <p className="font-bold text-sky-100 text-sm">Live Orchestrator SSE Mode Active</p>
                  {isStreamingLive && (
                    <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-sky-500/20 text-sky-300 border border-sky-500/40 animate-pulse">
                      <Radio className="h-2.5 w-2.5" />
                      <span>STREAMING SAGA...</span>
                    </span>
                  )}
                  {liveConnectionStatus === 'completed' && (
                    <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                      <CheckCircle2 className="h-2.5 w-2.5" />
                      <span>SAGA DECIDED</span>
                    </span>
                  )}
                </div>
                <p className="text-sky-300/80 mt-0.5">
                  Submissions post directly to <code className="font-mono bg-sky-900/60 px-1 py-0.5 rounded">POST /submit</code> and receive live event streams over Server-Sent Events (<code className="font-mono bg-sky-900/60 px-1 py-0.5 rounded">GET /permits/{'{id}'}/stream</code>).
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
              {livePermitId && (
                <span className="bg-sky-900/60 border border-sky-700/60 px-2.5 py-1 rounded-lg text-sky-200">
                  Permit: {livePermitId}
                </span>
              )}
              {liveTraceId && (
                <span className="bg-sky-900/60 border border-sky-700/60 px-2.5 py-1 rounded-lg text-sky-200">
                  Trace: {liveTraceId}
                </span>
              )}
              <span className="bg-slate-900/80 border border-slate-800 px-2.5 py-1 rounded-lg text-slate-300 font-sans">
                Events: <strong className="font-mono text-sky-300">{activeEvents.length}</strong>
              </span>
            </div>
          </div>
        )}

        {liveError && (
          <div className="p-4 rounded-xl bg-rose-950/40 border border-rose-500/40 text-xs text-rose-200 flex items-center space-x-2">
            <AlertCircle className="h-4 w-4 text-rose-400 shrink-0" />
            <span>{liveError}</span>
          </div>
        )}

        {/* Fault Injection Control Panel (Task T9) */}
        <FaultControlPanel
          onRunFaultDemo={handleRunFaultDemo}
          isBackendOnline={isBackendOnline}
        />

        {/* Fixture Scenario Selector (Fixture Mode) */}
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
              {(isSimulating || isStreamingLive) && (
                <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-mono bg-sky-500/20 text-sky-300 border border-sky-500/40 animate-pulse">
                  <Activity className="h-3 w-3" />
                  <span>Streaming Events...</span>
                </span>
              )}
            </h2>
            <div className="flex items-center space-x-2 text-xs">
              <button
                type="button"
                onClick={() => {
                  if (mode === 'live') {
                    handleSubmitLive();
                  } else {
                    handleSimulateReplay(selectedScenario);
                  }
                }}
                disabled={isSimulating || isSubmittingLive || isStreamingLive}
                className="px-3 py-1 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 flex items-center space-x-1.5 transition-colors disabled:opacity-50"
              >
                <RotateCcw className="h-3 w-3" />
                <span>{mode === 'live' ? 'Re-Run Live Saga' : 'Replay Trace'}</span>
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
          isSubmitting={isSimulating || isSubmittingLive || isStreamingLive}
          mode={mode}
        />
      </main>

      {/* Side Drawer for Span Inspection */}
      <SpanDetailDrawer span={selectedSpan} onClose={() => setSelectedSpan(null)} />
    </div>
  );
};

export default App;
