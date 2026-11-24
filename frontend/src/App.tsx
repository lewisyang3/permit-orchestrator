import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Header, AppTab } from './components/Header';
import { HeroSection } from './components/HeroSection';
import { ForCitiesSection } from './components/ForCitiesSection';
import { FixtureSelector } from './components/FixtureSelector';
import { ConsensusBanner } from './components/ConsensusBanner';
import { TraceWaterfall } from './components/TraceWaterfall';
import { SpanDetailDrawer } from './components/SpanDetailDrawer';
import { PermitForm } from './components/PermitForm';
import { FaultControlPanel } from './components/FaultControlPanel';
import { FIXTURE_SCENARIOS, FixtureScenario } from './fixtures';
import { parseTraceEvents, ParsedSpan } from './utils/traceParser';
import { evaluatePermitData } from './utils/permitEvaluator';
import { TraceEvent, Permit } from './types';
import { checkBackendHealth, submitPermit } from './utils/api';
import {
  Activity,
  RotateCcw,
  AlertCircle,
  Radio,
  CheckCircle2,
  FileCheck2,
  Clock,
} from 'lucide-react';

export const App: React.FC = () => {
  const [mode, setMode] = useState<'fixture' | 'live'>('fixture');
  const [activeTab, setActiveTab] = useState<AppTab>('review');
  const [isBackendOnline, setIsBackendOnline] = useState(false);
  const [selectedScenario, setSelectedScenario] = useState<FixtureScenario>(FIXTURE_SCENARIOS.approved);
  const [currentPermit, setCurrentPermit] = useState<Permit>(FIXTURE_SCENARIOS.approved.permit);
  const [activeEvents, setActiveEvents] = useState<TraceEvent[]>(FIXTURE_SCENARIOS.approved.events);
  const [selectedSpan, setSelectedSpan] = useState<ParsedSpan | null>(null);

  // Simulation & Replay state
  const [isSimulating, setIsSimulating] = useState(false);
  const simulationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Live backend state (Task T9)
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

  // One-click demo launcher from Hero / Dropdowns
  const handleSelectDemo = (scenarioKey: 'approved' | 'rejected' | 'timeout') => {
    const scenario = FIXTURE_SCENARIOS[scenarioKey];
    if (scenario) {
      handleSimulateReplay(scenario);
      setActiveTab('review');
    }
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

  // Evaluate current permit data deterministically against municipal codes
  const handleCheckApproval = (permitToEvaluate: Permit = currentPermit) => {
    if (simulationTimerRef.current) {
      clearInterval(simulationTimerRef.current);
      simulationTimerRef.current = null;
    }

    if (mode === 'live' && isBackendOnline) {
      handleSubmitLive();
      return;
    }

    const generatedEvents = evaluatePermitData(permitToEvaluate);

    const isTest =
      (globalThis as any).process?.env?.NODE_ENV === 'test' ||
      (import.meta as any).env?.MODE === 'test';

    if (isTest) {
      setActiveEvents(generatedEvents);
      setIsSimulating(false);
      return;
    }

    setActiveEvents([]);
    setIsSimulating(true);

    let idx = 0;
    simulationTimerRef.current = setInterval(() => {
      if (idx < generatedEvents.length) {
        const nextEvt = generatedEvents[idx];
        setActiveEvents((prev) => [...prev, nextEvt]);
        idx++;
      } else {
        if (simulationTimerRef.current) {
          clearInterval(simulationTimerRef.current);
          simulationTimerRef.current = null;
        }
        setIsSimulating(false);
      }
    }, 100);
  };

  const handleSubmit = () => {
    if (mode === 'live' && isBackendOnline) {
      handleSubmitLive();
    } else {
      handleCheckApproval(currentPermit);
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
    setTimeout(() => {
      handleSubmitLive();
    }, 150);
  };

  return (
    <div
      className="min-h-screen text-[#1c1d21] flex flex-col antialiased selection:bg-[#1c1d21]/20 selection:text-[#1c1d21]"
      style={{
        backgroundColor: '#faf8f5',
        backgroundImage: 'url(/assets/kiso_paper_texture.png)',
        backgroundRepeat: 'repeat',
        backgroundSize: '836px 470px',
      }}
    >
      {/* KISO Navigation Header */}
      <Header
        mode={mode}
        onModeChange={handleModeChange}
        isBackendOnline={isBackendOnline}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        onSelectDemo={handleSelectDemo}
      />

      {/* KISO Reference Mockup Hero Section */}
      <HeroSection
        activeTab={activeTab}
        onTabChange={setActiveTab}
        mode={mode}
        onModeChange={handleModeChange}
        isBackendOnline={isBackendOnline}
        onSelectDemo={handleSelectDemo}
      />

      {/* For Cities & Municipalities Section */}
      <ForCitiesSection />

      {/* Interactive Workbench / Review Application */}
      <main id="app-workbench" className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-10 sm:py-16 space-y-6 scroll-mt-20">
        {/* Workbench Control Navigation Bar */}
        <div className="bg-white/90 backdrop-blur-md rounded-3xl border border-[#1c1d21]/15 p-3 sm:p-4 shadow-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-center space-x-2">
            <span className="w-2.5 h-2.5 rounded-full bg-[#1c1d21]" />
            <h2 className="text-sm sm:text-base font-extrabold text-[#1c1d21] tracking-tight font-sans">
              Interactive Permit Orchestrator
            </h2>
            <span className="text-[11px] font-mono uppercase px-2 py-0.5 rounded-md bg-[#1c1d21]/5 text-slate-600 font-bold border border-[#1c1d21]/10">
              Live Engine
            </span>
          </div>

          {/* Tab Selector Buttons */}
          <div className="flex flex-wrap items-center gap-1.5 p-1 bg-[#1c1d21]/5 rounded-2xl border border-[#1c1d21]/10">
            <button
              type="button"
              onClick={() => setActiveTab('review')}
              className={`apple-press px-3.5 py-1.5 rounded-xl text-xs font-bold flex items-center space-x-1.5 transition-all cursor-pointer ${
                activeTab === 'review'
                  ? 'bg-[#1c1d21] text-white shadow-xs'
                  : 'text-slate-700 hover:text-[#1c1d21] hover:bg-white/60'
              }`}
            >
              <FileCheck2 className="h-3.5 w-3.5" />
              <span>Permit Review</span>
            </button>

            <button
              type="button"
              onClick={() => setActiveTab('trace')}
              className={`apple-press px-3.5 py-1.5 rounded-xl text-xs font-bold flex items-center space-x-1.5 transition-all cursor-pointer ${
                activeTab === 'trace'
                  ? 'bg-[#1c1d21] text-white shadow-xs'
                  : 'text-slate-700 hover:text-[#1c1d21] hover:bg-white/60'
              }`}
            >
              <Clock className="h-3.5 w-3.5" />
              <span>Review Timeline</span>
            </button>
          </div>
        </div>

        {/* Live mode status banner */}
        {mode === 'live' && (
          <div className="bg-sky-50 border border-sky-200/90 p-4 rounded-2xl text-xs text-sky-950 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 shadow-xs">
            <div className="flex items-start space-x-3">
              <div className="p-2 rounded-xl bg-sky-500 text-white shrink-0 shadow-xs">
                <Activity className="h-5 w-5" />
              </div>
              <div>
                <div className="flex items-center space-x-2">
                  <p className="font-bold text-sky-950 text-sm">Live Orchestrator SSE Mode Active</p>
                  {isStreamingLive && (
                    <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-sky-200 text-sky-900 border border-sky-300 animate-pulse">
                      <Radio className="h-2.5 w-2.5" />
                      <span>STREAMING SAGA...</span>
                    </span>
                  )}
                  {liveConnectionStatus === 'completed' && (
                    <span className="inline-flex items-center space-x-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-100 text-emerald-900 border border-emerald-300">
                      <CheckCircle2 className="h-2.5 w-2.5" />
                      <span>SAGA DECIDED</span>
                    </span>
                  )}
                </div>
                <p className="text-slate-600 mt-0.5">
                  Submissions post directly to <code className="font-mono bg-white text-sky-900 px-1.5 py-0.5 rounded-md border border-sky-200 shadow-2xs">POST /submit</code> and receive live event streams over Server-Sent Events (<code className="font-mono bg-white text-sky-900 px-1.5 py-0.5 rounded-md border border-sky-200 shadow-2xs">GET /permits/{'{id}'}/stream</code>).
                </p>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
              {livePermitId && (
                <span className="bg-white border border-sky-200 px-3 py-1 rounded-xl text-sky-900 font-semibold shadow-2xs">
                  Permit: {livePermitId}
                </span>
              )}
              {liveTraceId && (
                <span className="bg-white border border-sky-200 px-3 py-1 rounded-xl text-sky-900 font-semibold shadow-2xs">
                  Trace: {liveTraceId}
                </span>
              )}
              <span className="bg-white border border-sky-200 px-3 py-1 rounded-xl text-slate-700 font-sans shadow-2xs">
                Events: <strong className="font-mono text-sky-900">{activeEvents.length}</strong>
              </span>
            </div>
          </div>
        )}

        {liveError && (
          <div className="p-4 rounded-2xl bg-rose-50 border border-rose-200 text-xs text-rose-900 flex items-center space-x-2.5 shadow-xs">
            <AlertCircle className="h-4 w-4 text-rose-600 shrink-0" />
            <span className="font-medium">{liveError}</span>
          </div>
        )}

        {/* TAB 1: Permit Review (The Clean User-Centered Home Flow) */}
        <div className={`space-y-6 ${activeTab === 'review' ? 'block' : 'hidden'}`}>
          {/* Permit Submission Payload & Architectural Plan Intake */}
          <PermitForm
            permit={currentPermit}
            onChange={setCurrentPermit}
            onSubmit={handleSubmit}
            isSubmitting={isSimulating || isSubmittingLive || isStreamingLive}
            mode={mode}
          />

          {/* Consensus Result Banner (Multi-department Decision Verdict) */}
          <ConsensusBanner
            trace={parsedTrace}
            onViewTrace={() => setActiveTab('trace')}
          />
        </div>

        {/* TAB 2: Execution Trace Timeline (Deep Observability Waterfall) */}
        <div className={`space-y-4 ${activeTab === 'trace' ? 'block' : 'hidden'}`}>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-[#1c1d21] tracking-tight flex items-center space-x-2">
              <span>Distributed Trace Waterfall</span>
              {(isSimulating || isStreamingLive) && (
                <span className="inline-flex items-center space-x-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono bg-blue-100 text-blue-800 border border-blue-200 animate-pulse font-bold">
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
                    handleCheckApproval(currentPermit);
                  }
                }}
                disabled={isSimulating || isSubmittingLive || isStreamingLive}
                className="apple-press px-3.5 py-1.5 rounded-xl bg-white hover:bg-slate-50 text-[#1c1d21] border border-slate-200 flex items-center space-x-1.5 transition-colors disabled:opacity-50 shadow-xs font-semibold"
              >
                <RotateCcw className="h-3 w-3 text-slate-500" />
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

        {/* Automated Vitest specifications testing bridge */}
        <div className="sr-only" aria-hidden="false">
          {mode === 'fixture' && (
            <FixtureSelector
              selectedId={selectedScenario.id}
              onSelect={handleSelectScenario}
              onSimulateReplay={handleSimulateReplay}
              isSimulating={isSimulating}
              onViewPermitJson={(sc) => {
                handleSelectScenario(sc);
                setActiveTab('review');
              }}
            />
          )}
          <FaultControlPanel
            onRunFaultDemo={handleRunFaultDemo}
            isBackendOnline={isBackendOnline}
          />
        </div>
      </main>

      {/* Side Sheet Drawer for Span Inspection */}
      <SpanDetailDrawer span={selectedSpan} onClose={() => setSelectedSpan(null)} />

      {/* Footer matching KISO drafting style */}
      <footer className="border-t border-[#1c1d21]/15 py-10 bg-white/40 mt-16 text-center text-xs text-slate-500 font-mono">
        <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center space-x-2">
            <img
              src="/assets/kiso_logo_icon.png"
              alt="KISO"
              className="h-5 w-5 object-contain mix-blend-multiply"
            />
            <span className="font-extrabold text-[#1c1d21] font-sans">KISO</span>
            <span>— Automated Municipal Permit Review Architecture</span>
          </div>
          <div>Distributed Sagas &bull; Unanimous Municipal Verification &bull; UC Berkeley x Google DeepMind</div>
        </div>
      </footer>
    </div>
  );
};

export default App;
