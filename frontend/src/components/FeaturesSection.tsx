import React from 'react';
import {
  ShieldCheck,
  Building2,
  Flame,
  Droplet,
  Layers,
  Activity,
  CheckCircle,
  Landmark,
} from 'lucide-react';

export const FeaturesSection: React.FC = () => {
  return (
    <section id="features" className="py-16 sm:py-24 border-b border-[#1c1d21]/15 scroll-mt-20">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-20">
        {/* Core Features Overview */}
        <div>
          <div className="text-center max-w-2xl mx-auto space-y-3 mb-14">
            <div className="inline-flex items-center space-x-1.5 px-3 py-1 rounded-full border border-[#1c1d21]/20 bg-white/60 text-[#1c1d21] font-mono text-xs uppercase tracking-wider font-bold">
              <span>CORE CAPABILITIES</span>
            </div>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-[#1c1d21] tracking-tight">
              Engineered for Municipal Rigor &amp; Speed
            </h2>
            <p className="text-sm sm:text-base text-[#474a51] leading-relaxed">
              Every check is backed by deterministic city code tables, immutable audit trails, and distributed saga resilience.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {/* Feature 1 */}
            <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 shadow-xs hover:border-[#1c1d21]/40 transition-colors">
              <div className="p-3 rounded-2xl bg-indigo-50 text-indigo-700 w-fit mb-4">
                <Building2 className="h-5 w-5" />
              </div>
              <h3 className="text-base font-bold text-[#1c1d21] mb-1.5">
                Automated Zoning Review
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Instantly validates building heights (&le; 35&apos;), rear setbacks (&ge; 10&apos;), and maximum lot coverage (&le; 40%) against specific district zones (R-1, C-1, M-1).
              </p>
            </div>

            {/* Feature 2 */}
            <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 shadow-xs hover:border-[#1c1d21]/40 transition-colors">
              <div className="p-3 rounded-2xl bg-rose-50 text-rose-700 w-fit mb-4">
                <Flame className="h-5 w-5" />
              </div>
              <h3 className="text-base font-bold text-[#1c1d21] mb-1.5">
                Life Safety &amp; Fire Protection
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Enforces automatic fire suppression sprinkler requirements for commercial occupancy and calculates minimum egress exit capacities according to NFPA and city fire codes.
              </p>
            </div>

            {/* Feature 3 */}
            <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 shadow-xs hover:border-[#1c1d21]/40 transition-colors">
              <div className="p-3 rounded-2xl bg-sky-50 text-sky-700 w-fit mb-4">
                <Droplet className="h-5 w-5" />
              </div>
              <h3 className="text-base font-bold text-[#1c1d21] mb-1.5">
                Water &amp; Stormwater Compliance
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Verifies water main meter capacity, cross-connection backflow prevention devices, and stormwater runoff mitigation plans before construction groundbreak.
              </p>
            </div>

            {/* Feature 4 */}
            <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 shadow-xs hover:border-[#1c1d21]/40 transition-colors">
              <div className="p-3 rounded-2xl bg-emerald-50 text-emerald-700 w-fit mb-4">
                <Layers className="h-5 w-5" />
              </div>
              <h3 className="text-base font-bold text-[#1c1d21] mb-1.5">
                Structural &amp; Seismic Design
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Validates story limits, construction classifications (Type V wood-frame vs. Type I fire-resistive), deep pile vs. slab foundations, and seismic design categories (A through D).
              </p>
            </div>

            {/* Feature 5 */}
            <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 shadow-xs hover:border-[#1c1d21]/40 transition-colors">
              <div className="p-3 rounded-2xl bg-purple-50 text-purple-700 w-fit mb-4">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <h3 className="text-base font-bold text-[#1c1d21] mb-1.5">
                Two-Phase Consensus Protocol
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Zero partial approvals. If any single department is offline, times out, or detects a code violation, KISO executes a clean saga compensation to protect public safety.
              </p>
            </div>

            {/* Feature 6 */}
            <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 shadow-xs hover:border-[#1c1d21]/40 transition-colors">
              <div className="p-3 rounded-2xl bg-amber-50 text-amber-700 w-fit mb-4">
                <Activity className="h-5 w-5" />
              </div>
              <h3 className="text-base font-bold text-[#1c1d21] mb-1.5">
                Real-Time Trace Observability
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Reviewers watch live distributed traces stream over Server-Sent Events (SSE). Millisecond span latencies, retry attempts, and raw payloads are fully transparent.
              </p>
            </div>
          </div>
        </div>

        {/* FOR CITIES SECTION */}
        <div id="for-cities" className="pt-10 scroll-mt-20">
          <div className="bg-[#1c1d21] text-white rounded-3xl p-8 sm:p-12 relative overflow-hidden shadow-xl">
            {/* Architectural grid overlay */}
            <div
              className="absolute inset-0 opacity-10 pointer-events-none"
              style={{
                backgroundImage: 'radial-gradient(rgba(255,255,255,0.4) 1px, transparent 0)',
                backgroundSize: '24px 24px',
              }}
            />

            <div className="relative z-10 grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
              <div className="lg:col-span-7 space-y-4">
                <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full bg-white/10 text-white/90 font-mono text-xs uppercase tracking-wider font-bold">
                  <Landmark className="h-3.5 w-3.5 text-white/80" />
                  <span>FOR CITIES &amp; MUNICIPALITIES</span>
                </div>
                <h3 className="text-2xl sm:text-3xl font-extrabold tracking-tight">
                  Cut Review Queues from Months to Minutes
                </h3>
                <p className="text-sm text-slate-300 leading-relaxed max-w-xl">
                  Building departments face unprecedented permitting volumes. KISO automates the repetitive preliminary plan-check, freeing licensed municipal plan examiners to focus on complex architectural judgments.
                </p>
                <div className="pt-2 flex flex-wrap gap-4 text-xs font-mono">
                  <div className="flex items-center space-x-2">
                    <CheckCircle className="h-4 w-4 text-emerald-400" />
                    <span>85% Faster Plan Intake</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <CheckCircle className="h-4 w-4 text-emerald-400" />
                    <span>Exact Municipal Code Citations</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <CheckCircle className="h-4 w-4 text-emerald-400" />
                    <span>Zero Misplaced Applications</span>
                  </div>
                </div>
              </div>

              <div className="lg:col-span-5 grid grid-cols-2 gap-4">
                <div className="p-5 rounded-2xl bg-white/5 border border-white/10 text-center space-y-1">
                  <div className="text-3xl sm:text-4xl font-extrabold text-white font-mono">
                    &lt; 500ms
                  </div>
                  <div className="text-xs text-slate-400 font-medium">Evaluation Latency</div>
                </div>

                <div className="p-5 rounded-2xl bg-white/5 border border-white/10 text-center space-y-1">
                  <div className="text-3xl sm:text-4xl font-extrabold text-white font-mono">
                    100%
                  </div>
                  <div className="text-xs text-slate-400 font-medium">Audit Traceability</div>
                </div>

                <div className="p-5 rounded-2xl bg-white/5 border border-white/10 text-center space-y-1">
                  <div className="text-3xl sm:text-4xl font-extrabold text-white font-mono">
                    4 Depts
                  </div>
                  <div className="text-xs text-slate-400 font-medium">Parallel Consensus</div>
                </div>

                <div className="p-5 rounded-2xl bg-white/5 border border-white/10 text-center space-y-1">
                  <div className="text-3xl sm:text-4xl font-extrabold text-white font-mono">
                    0 Drift
                  </div>
                  <div className="text-xs text-slate-400 font-medium">Deterministic Rules</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
