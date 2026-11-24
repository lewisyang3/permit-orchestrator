import React from 'react';
import { UploadCloud, Network, FileCheck2, ArrowRight } from 'lucide-react';

export const HowItWorks: React.FC = () => {
  return (
    <section id="how-it-works" className="py-16 sm:py-24 border-b border-[#1c1d21]/15 scroll-mt-20">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Section Header */}
        <div className="text-center max-w-2xl mx-auto space-y-3 mb-14">
          <div className="inline-flex items-center space-x-1.5 px-3 py-1 rounded-full border border-[#1c1d21]/20 bg-white/60 text-[#1c1d21] font-mono text-xs uppercase tracking-wider font-bold">
            <span>HOW IT WORKS</span>
          </div>
          <h2 className="text-3xl sm:text-4xl font-extrabold text-[#1c1d21] tracking-tight">
            From Blueprint to Final Consensus in Seconds
          </h2>
          <p className="text-sm sm:text-base text-[#474a51] leading-relaxed">
            KISO replaces manual routing and weeks of city hall backlog with deterministic, distributed code evaluation engines.
          </p>
        </div>

        {/* 3 Step Process Cards with Blueprint Aesthetic */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 relative">
          {/* Step 1 */}
          <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 sm:p-7 relative shadow-xs flex flex-col justify-between hover:shadow-md transition-shadow">
            <div>
              <div className="flex items-center justify-between mb-4">
                <span className="font-mono text-xs font-bold text-slate-400">STAGE 01</span>
                <span className="p-2.5 rounded-2xl bg-[#1c1d21]/5 text-[#1c1d21]">
                  <UploadCloud className="h-5 w-5" />
                </span>
              </div>
              <h3 className="text-lg font-bold text-[#1c1d21] mb-2">
                Plan Ingestion &amp; Parameter Extraction
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Upload construction drawings, PDF blueprints, or municipal application forms. Gemini-powered vision and heuristic extractors parse lot coverage, setbacks, sprinkler requirements, and seismic categories into structured payloads.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-[#1c1d21]/10 flex items-center text-[11px] font-mono text-slate-500">
              <span>Input: PDF / CAD / Text</span>
              <ArrowRight className="h-3 w-3 ml-auto text-slate-400" />
            </div>
          </div>

          {/* Step 2 */}
          <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 sm:p-7 relative shadow-xs flex flex-col justify-between hover:shadow-md transition-shadow">
            <div>
              <div className="flex items-center justify-between mb-4">
                <span className="font-mono text-xs font-bold text-slate-400">STAGE 02</span>
                <span className="p-2.5 rounded-2xl bg-[#1c1d21]/5 text-[#1c1d21]">
                  <Network className="h-5 w-5" />
                </span>
              </div>
              <h3 className="text-lg font-bold text-[#1c1d21] mb-2">
                Concurrent Department Sagas
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                The permit orchestrator fans out parallel review sagas across Zoning, Fire Safety, Water/Environmental, and Structural departments. Each service independently checks municipal codes with timeout protection and retry policies.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-[#1c1d21]/10 flex items-center text-[11px] font-mono text-slate-500">
              <span>Concurrent Distributed Sagas</span>
              <ArrowRight className="h-3 w-3 ml-auto text-slate-400" />
            </div>
          </div>

          {/* Step 3 */}
          <div className="bg-white/80 backdrop-blur-sm border border-[#1c1d21]/15 rounded-3xl p-6 sm:p-7 relative shadow-xs flex flex-col justify-between hover:shadow-md transition-shadow">
            <div>
              <div className="flex items-center justify-between mb-4">
                <span className="font-mono text-xs font-bold text-slate-400">STAGE 03</span>
                <span className="p-2.5 rounded-2xl bg-[#1c1d21]/5 text-[#1c1d21]">
                  <FileCheck2 className="h-5 w-5" />
                </span>
              </div>
              <h3 className="text-lg font-bold text-[#1c1d21] mb-2">
                Sourced Analysis &amp; Consensus Verdict
              </h3>
              <p className="text-xs text-[#5c5f66] leading-relaxed">
                Zero partial approvals: all departments must reach consensus. The reviewer receives exact municipal code citations, violation explanations, and an immutable distributed trace audit log for municipal record-keeping.
              </p>
            </div>
            <div className="mt-6 pt-4 border-t border-[#1c1d21]/10 flex items-center text-[11px] font-mono text-slate-500">
              <span>Unanimous Consensus Verdict</span>
              <ArrowRight className="h-3 w-3 ml-auto text-slate-400" />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
