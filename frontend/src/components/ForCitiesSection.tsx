import React from 'react';
import { Landmark, CheckCircle } from 'lucide-react';

export const ForCitiesSection: React.FC = () => {
  return (
    <section id="for-cities" className="py-12 sm:py-16 border-b border-[#1c1d21]/15 scroll-mt-20">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
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

            <div className="lg:col-span-5 grid grid-cols-1 sm:grid-cols-2 gap-4">
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
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
