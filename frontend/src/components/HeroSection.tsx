import React from 'react';
import { ArrowRight, Play } from 'lucide-react';
import { AppTab } from './Header';

export interface HeroSectionProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  mode: 'fixture' | 'live';
  onModeChange: (mode: 'fixture' | 'live') => void;
  isBackendOnline: boolean;
  onSelectDemo: (scenarioId: 'approved' | 'rejected' | 'timeout') => void;
}

export const HeroSection: React.FC<HeroSectionProps> = ({
  onTabChange,
  onSelectDemo,
}) => {
  const handleGetStarted = () => {
    onTabChange('review');
    const workbenchEl = document.getElementById('app-workbench');
    if (workbenchEl) {
      workbenchEl.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const handleSeeDemo = () => {
    onSelectDemo('approved');
    const workbenchEl = document.getElementById('app-workbench');
    if (workbenchEl) {
      workbenchEl.scrollIntoView({ behavior: 'smooth' });
    }
  };

  return (
    <section className="relative overflow-hidden min-h-[calc(100vh-5rem)] sm:min-h-[calc(100vh-6rem)] flex flex-col justify-center pt-8 sm:pt-14 pb-12 sm:pb-20 border-b border-[#1c1d21]/15">
      {/* Background paper texture & subtle blueprint perspective guidelines */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 w-full my-auto">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-12 items-center">
          {/* Left Column: Hero Copy & Actions */}
          <div className="lg:col-span-6 space-y-5 sm:space-y-6 z-10">
            {/* Header Lockup: Badge + Headline with tight architectural gap */}
            <div className="space-y-1.5 sm:space-y-2">
              {/* Top Badge: PERMIT REVIEW, AUTOMATED with Drafting Corners */}
              <div className="inline-block">
                <div className="relative group">
                  <img
                    src="/assets/permit_review_automated.png"
                    alt="Permit Review, Automated"
                    className="h-10 sm:h-12 md:h-14 lg:h-16 w-auto max-w-full object-contain mix-blend-multiply drop-shadow-2xs select-none"
                  />
                </div>
              </div>

              {/* Main Headline: Speed up building permit reviews. */}
              <h1 className="text-4xl sm:text-5xl lg:text-[56px] font-extrabold text-[#1c1d21] tracking-tight leading-[1.08] font-sans">
                Speed up building <br className="hidden sm:inline" />
                permit reviews.
              </h1>
            </div>

            {/* Subtitle matching KISO.png */}
            <p className="text-base sm:text-lg text-[#474a51] font-normal leading-relaxed max-w-xl">
              Automatically check plans against city ordinances, identify issues, and give reviewers a clear, sourced analysis.
            </p>

            {/* Action Buttons: Get Started and See Demo matching KISO.png */}
            <div className="flex flex-wrap items-center gap-3.5 pt-2">
              {/* PRIMARY BUTTON: Get Started -> (Direct action, scrolls to review workbench) */}
              <button
                type="button"
                onClick={handleGetStarted}
                aria-label="Get Started"
                className="apple-press px-6 sm:px-7 py-3 rounded-2xl bg-[#222326] hover:bg-[#34353a] active:bg-[#151618] text-white text-sm font-semibold tracking-wide flex items-center space-x-2 transition-all shadow-md hover:shadow-lg cursor-pointer"
              >
                <span>Get Started</span>
                <ArrowRight className="h-4 w-4 text-white/90" />
              </button>

              {/* SECONDARY BUTTON: ▷ See Demo (Direct action, launches demo simulation & scrolls to workbench) */}
              <button
                type="button"
                onClick={handleSeeDemo}
                aria-label="See Demo"
                className="apple-press px-5 sm:px-6 py-3 rounded-2xl border border-[#222326]/40 hover:border-[#222326] bg-white/70 hover:bg-white text-[#222326] text-sm font-semibold flex items-center space-x-2 transition-all shadow-xs cursor-pointer"
              >
                <Play className="h-4 w-4 fill-[#222326] text-[#222326]" />
                <span>See Demo</span>
              </button>
            </div>

            {/* Quick architectural stat pills */}
            <div className="pt-2 flex flex-wrap items-center gap-3 text-xs text-[#5c5f66] font-mono">
              <span className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-[#1c1d21]/5 border border-[#1c1d21]/10">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-600" />
                <span>4 Municipal Review Engines</span>
              </span>
              <span className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-[#1c1d21]/5 border border-[#1c1d21]/10">
                <span className="w-1.5 h-1.5 rounded-full bg-[#1c1d21]" />
                <span>Zero Partial Approvals</span>
              </span>
              <span className="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg bg-[#1c1d21]/5 border border-[#1c1d21]/10">
                <span className="w-1.5 h-1.5 rounded-full bg-[#1c1d21]" />
                <span>Sub-second Trace Latency</span>
              </span>
            </div>
          </div>

          {/* Right Column: Architectural Building Sketch Visual matching KISO.png */}
          <div className="lg:col-span-6 relative flex items-center justify-center lg:justify-end">
            <div className="relative w-full max-w-xl lg:max-w-2xl">
              {/* The Architectural Pencil Sketch Asset with annotations */}
              <img
                src="/assets/kiso_building_sketch.png"
                alt="Architectural Blueprint Sketch with Height, Setback, and Lot Coverage annotations"
                className="w-full h-auto object-contain mix-blend-multiply select-none"
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
