"use client";

import type { SimulationReport } from "@/lib/schema";
import { DEBUG_MODE } from "@/lib/debug";
import { CompetitorAnalysisSection } from "./CompetitorAnalysisSection";
import { ConfidenceSection } from "./ConfidenceSection";
import { DebateShiftMarkers } from "./DebateShiftMarkers";
import { EvidenceLedger } from "./EvidenceLedger";
import { MarketAcceptanceSection } from "./MarketAcceptanceSection";
import { PersuasionAnalysisSection } from "./PersuasionAnalysisSection";
import { RecommendationsSection } from "./RecommendationsSection";
import { SentimentSection } from "./SentimentSection";
import { TrajectorySection } from "./TrajectorySection";

export function ReportShell({ report }: { report: SimulationReport }) {
  const sections: { id: string; label: string }[] = [
    { id: "sentiment", label: "1. Public opinion sentiment" },
    { id: "persuasion", label: "2/3. What persuaded / didn't" },
    { id: "acceptance", label: "4. Market acceptance" },
    { id: "trajectory", label: "5. Product trajectory" },
    { id: "competitors", label: "6. Competitor analysis" },
    { id: "recommendations", label: "7. Recommendations" },
    { id: "shifts", label: "8. Debate shifts" },
    { id: "confidence", label: "9. Confidence" },
    { id: "ledger", label: "9. Evidence ledger" },
  ];

  return (
    <div className="grid gap-8 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <aside className="hidden lg:block">
        <nav aria-label="Report sections" className="sticky top-8 space-y-1 text-xs">
          {sections.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              className="block rounded px-2 py-1 text-ink-600 hover:bg-ink-100 hover:text-ink-900"
            >
              {s.label}
            </a>
          ))}
        </nav>
      </aside>

      <div className="space-y-10">
        <header className="space-y-2 border-b border-ink-200 pb-6">
          <p className="text-xs uppercase tracking-widest text-ink-400">
            Status: {report.status} · schema {report.schema_version}
          </p>
          <h1 className="font-serif text-3xl tracking-tight">Simulation report</h1>
          <p className="text-xs text-ink-400">
            Generated {new Date(report.created_at).toLocaleString()}
          </p>
        </header>

        <SentimentSection
          id="sentiment"
          section={report.public_opinion_sentiment}
          details={report.evidence_anchor_details}
        />
        <PersuasionAnalysisSection
          id="persuasion"
          analysis={report.persuasion_analysis}
          details={report.evidence_anchor_details}
        />
        <MarketAcceptanceSection
          id="acceptance"
          section={report.market_acceptance_requirement}
          details={report.evidence_anchor_details}
        />
        <TrajectorySection
          id="trajectory"
          section={report.product_trajectory}
          details={report.evidence_anchor_details}
        />
        <CompetitorAnalysisSection
          id="competitors"
          section={report.competitor_analysis}
          details={report.evidence_anchor_details}
        />
        <RecommendationsSection
          id="recommendations"
          recommendations={report.recommendations}
          details={report.evidence_anchor_details}
        />
        <DebateShiftMarkers id="shifts" section={report.debate_shift_markers} />
        <ConfidenceSection id="confidence" section={report.confidence} />
        <EvidenceLedger
          id="ledger"
          section={report.evidence_ledger}
          details={report.evidence_anchor_details}
        />

        {DEBUG_MODE && <DebugPanel report={report} />}
      </div>
    </div>
  );
}

function DebugPanel({ report }: { report: SimulationReport }) {
  return (
    <details className="rounded border border-ink-200 bg-ink-100 p-4 text-xs">
      <summary className="cursor-pointer font-mono text-ink-600">Debug · raw report payload</summary>
      <pre className="mt-2 overflow-x-auto font-mono">
        {JSON.stringify(report, null, 2)}
      </pre>
    </details>
  );
}
