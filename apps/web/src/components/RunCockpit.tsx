"use client";
// Phase 10B+ — "God's eye" cockpit shown after a run completes.
// Composes:
//   - GodsEyeHeader (big title + exec summary + trajectory + consensus)
//   - View toggle (GRAPH / DEBATE / SPLIT)
//   - AgentGraph (left in SPLIT, full-width in GRAPH)
//   - DiscussionTranscript (right in SPLIT, full-width in DEBATE)
//   - LiveDistribution + OutcomeStats stacked on the right
//   - ReportDashboard rendered below

import { useEffect, useState } from "react";
import {
  getAssemblyDiscussionTurns,
  getAssemblyReport,
} from "@/lib/api";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
} from "@/lib/types";
import { AgentGraph } from "./AgentGraph";
import { AudienceFitCards } from "./AudienceFitCards";
import { DiscussionTranscript } from "./DiscussionTranscript";
import { FounderTakeaway } from "./FounderTakeaway";
import { GodsEyeHeader } from "./GodsEyeHeader";
import { LiveDistribution } from "./LiveDistribution";
import { OutcomeStats } from "./OutcomeStats";
import { ReportDashboard } from "./ReportDashboard";
import { WhyShiftedResistedCards } from "./WhyShiftedResistedCards";

type ViewMode = "graph" | "debate" | "split";

export interface RunCockpitProps {
  runId: string;
}

/** The founder's requested debate-agent count (preferred_society_size)
 *  survives only inside the loosely-typed product_brief bag echoed back
 *  on the report. It is absent on default runs, so this returns
 *  undefined unless the founder explicitly set a number. */
function readPreferredSocietySize(
  report: FounderReport | null,
): number | undefined {
  const raw = report?.product_brief?.["preferred_society_size"];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : undefined;
}

export function RunCockpit({ runId }: RunCockpitProps) {
  const [transcript, setTranscript] =
    useState<DiscussionTranscriptPayload | null>(null);
  const [report, setReport] = useState<FounderReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("split");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getAssemblyDiscussionTurns(runId),
      getAssemblyReport(runId).catch(() => null),
    ])
      .then(([t, r]) => {
        if (cancelled) return;
        setTranscript(t);
        setReport(r);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Unknown error");
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const hasTranscript = !!transcript && transcript.groups.length > 0;
  const requestedAgentCount = readPreferredSocietySize(report);

  return (
    <div className="space-y-6">
      {/* God's-eye header — only renders once both report + transcript
          loaded. Until then we show a quiet skeleton so the page
          doesn't jump. */}
      {report && transcript && hasTranscript ? (
        <>
          <GodsEyeHeader report={report} transcript={transcript} />
          {/* Founder takeaway — plain-English summary right under the
              header so the founder reads "what does this mean for me"
              before any chart. */}
          <FounderTakeaway report={report} transcript={transcript} />
          {/* Best-fit + hardest-to-convince audiences — synthesized
              from cohort/role data. */}
          <AudienceFitCards transcript={transcript} />
          {/* Why opinions shifted vs why some stayed resistant. */}
          <WhyShiftedResistedCards
            report={report}
            transcript={transcript}
          />
        </>
      ) : null}

      {/* View toggle. Run ID is intentionally NOT shown by default —
          founders don't need to see internal identifiers on the main
          report. It's tucked into a small "Technical details"
          disclosure for anyone debugging. */}
      <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-surface px-4 py-3">
        <details className="text-xs text-text-muted">
          <summary
            className="cursor-pointer select-none"
            data-testid="technical-details-toggle"
          >
            Technical details
          </summary>
          <div className="mt-2 font-mono">
            run id:{" "}
            <span data-testid="run-id" className="text-text-body">
              {runId}
            </span>
          </div>
        </details>
        <div
          className="flex gap-1.5 text-xs"
          data-testid="view-toggle"
          role="tablist"
        >
          {(["graph", "debate", "split"] as ViewMode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={view === mode}
              onClick={() => setView(mode)}
              data-testid={`view-${mode}`}
              className={`rounded-md border px-3 py-1.5 uppercase tracking-wider transition-colors ${
                view === mode
                  ? "border-accent-border bg-accent-soft text-accent"
                  : "border-border text-text-muted hover:border-accent-border/40"
              }`}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>

      {/* Cockpit body */}
      {error ? (
        <div
          role="alert"
          className="rounded-md border border-danger/40 bg-surface px-4 py-3 text-sm text-danger"
        >
          Could not load cockpit data: {error}
        </div>
      ) : !transcript ? (
        <div className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-text-muted">
          Loading cockpit data…
        </div>
      ) : !hasTranscript ? (
        <div
          className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-text-muted"
          data-testid="cockpit-empty"
        >
          {transcript.note ??
            "No transcript available for this run. Switch to live_founder_brief mode for the per-turn cockpit."}
        </div>
      ) : (
        <CockpitGrid
          view={view}
          transcript={transcript}
          runId={runId}
          requestedAgentCount={requestedAgentCount}
        />
      )}

      {/* Always show the structured report sections below the cockpit.
          We hand the transcript down so the personas card can derive
          a humanized role + stance breakdown directly from it. */}
      <ReportDashboard runId={runId} transcript={transcript} />
    </div>
  );
}

function CockpitGrid({
  view,
  transcript,
  runId,
  requestedAgentCount,
}: {
  view: ViewMode;
  transcript: DiscussionTranscriptPayload;
  runId: string;
  requestedAgentCount?: number;
}) {
  // The right column stacks LiveDistribution + OutcomeStats so the
  // founder always sees both signals (per-round bucket bars + the
  // shifted-vs-held outcome) regardless of view.
  const rightColumn = (
    <div className="space-y-4">
      <LiveDistribution transcript={transcript} />
      <OutcomeStats transcript={transcript} />
    </div>
  );
  if (view === "graph") {
    return (
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <AgentGraph
          transcript={transcript}
          requestedAgentCount={requestedAgentCount}
        />
        {rightColumn}
      </div>
    );
  }
  if (view === "debate") {
    return (
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <DiscussionTranscript runId={runId} transcript={transcript} />
        {rightColumn}
      </div>
    );
  }
  // split — graph | transcript | (live distribution + outcome stats)
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)_320px]">
      <AgentGraph
        transcript={transcript}
        requestedAgentCount={requestedAgentCount}
      />
      <DiscussionTranscript runId={runId} transcript={transcript} />
      {rightColumn}
    </div>
  );
}
