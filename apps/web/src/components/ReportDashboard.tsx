"use client";
// Phase 10B — founder report dashboard. Composes IntentSnapshot,
// CohortCards, PersonaList, DiscussionSummary, plus the report's
// objections/proof/persuasion levers + recommended_next_tests +
// caveats. Renders a download link for the markdown report.

import { useEffect, useState } from "react";
import {
  getAssemblyCohorts,
  getAssemblyDiscussion,
  getAssemblyIntent,
  getAssemblyPersonas,
  getAssemblyReport,
} from "@/lib/api";
import {
  filterApplicableObjectionBuckets,
  filterApplicableProofBuckets,
  objectionSentence,
  proofSentence,
} from "@/lib/buckets";
import type {
  CohortsPayload,
  DiscussionPayload,
  DiscussionTranscriptPayload,
  FounderReport,
  IntentPayload,
  PersonasPayload,
} from "@/lib/types";
import { CaveatBanner } from "./CaveatBanner";
import { DiscussionSummary } from "./DiscussionSummary";
import { EvidenceBaseCard } from "./EvidenceBaseCard";
import { IntentSnapshot } from "./IntentSnapshot";
import { LightweightVoterPanelLive } from "./LightweightVoterPanelLive";
import { PersonaList } from "./PersonaList";
import { ReportActions } from "./ReportActions";

export interface ReportDashboardProps {
  runId: string;
  /** When provided (e.g. from RunCockpit), the persona card uses
   *  this to render a humanized role + stance breakdown. */
  transcript?: DiscussionTranscriptPayload | null;
}

export function ReportDashboard({
  runId,
  transcript,
}: ReportDashboardProps) {
  const [report, setReport] = useState<FounderReport | null>(null);
  const [intent, setIntent] = useState<IntentPayload | null>(null);
  const [cohorts, setCohorts] = useState<CohortsPayload | null>(null);
  const [personas, setPersonas] = useState<PersonasPayload | null>(null);
  const [discussion, setDiscussion] = useState<DiscussionPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getAssemblyReport(runId),
      getAssemblyIntent(runId),
      getAssemblyCohorts(runId),
      getAssemblyPersonas(runId),
      getAssemblyDiscussion(runId),
    ])
      .then(([r, i, c, p, d]) => {
        if (cancelled) return;
        setReport(r);
        setIntent(i);
        setCohorts(c);
        setPersonas(p);
        setDiscussion(d);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Unknown error");
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-md border border-danger/40 bg-surface px-4 py-3 text-sm text-danger"
      >
        Could not load report: {error}
      </div>
    );
  }

  if (!report) {
    return (
      <div className="rounded-md border border-border bg-surface px-4 py-3 text-sm text-text-muted">
        Loading report…
      </div>
    );
  }

  return (
    <div className="space-y-8" data-testid="report-dashboard">
      {/* Phase 10B.5 — report actions cluster: Copy link, Download
          in-depth report, Run another product. */}
      <ReportActions
        runId={runId}
        productName={
          (report.product_brief?.product_name as string | undefined) ||
          undefined
        }
        report={report}
        intent={intent}
        cohorts={cohorts}
        personas={personas}
        discussion={discussion}
        transcript={transcript}
      />

      {/* Evidence base — small reassurance card showing what the
          synthetic society is grounded in. */}
      <EvidenceBaseCard runId={runId} personas={personas} />

      {/* Intent + Cohorts row */}
      {intent ? (
        <IntentSnapshot
          intentDistribution={intent.intent_distribution ?? {}}
          switchingDistribution={intent.switching_status_distribution}
          societySize={report.synthetic_society_size}
        />
      ) : null}

      {/* Phase 14A — 100-voter influence overlay (renders null on
          empty state so old runs without the artifact still render
          the rest of the dashboard). Placed AFTER intent snapshot,
          BEFORE the objections/debate sections per spec. */}
      <LightweightVoterPanelLive runId={runId} />

      {/* Objections + Proof — natural-language sentences, no scores */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NaturalLanguageList
          title="What this society pushed back on"
          subtitle="Synthetic objections, ordered by how often they came up."
          items={filterApplicableObjectionBuckets(
            report.top_objections,
            report.product_brief,
          )}
          render={(b) => objectionSentence(b, report.product_brief)}
          tone="muted"
        />
        <NaturalLanguageList
          title="What would change their minds"
          subtitle="Synthetic proof needs, ordered by how much they'd shift the room."
          items={filterApplicableProofBuckets(
            report.proof_needed,
            report.product_brief,
          )}
          render={(b) => proofSentence(b)}
          tone="accent"
        />
      </section>

      {/* Personas + Discussion */}
      {personas ? (
        <PersonaList
          personas={personas}
          transcript={transcript}
          runId={runId}
          productName={
            (report.product_brief?.product_name as string | undefined) ||
            undefined
          }
          report={report}
          intent={intent}
          cohorts={cohorts}
          discussion={discussion}
        />
      ) : null}
      {discussion ? (
        <DiscussionSummary discussion={discussion} report={report} />
      ) : null}

      {/* Caveats */}
      <CaveatBanner caveats={report.caveats?.length ? report.caveats : undefined} />
    </div>
  );
}

function NaturalLanguageList({
  title,
  subtitle,
  items,
  render,
  tone,
}: {
  title: string;
  subtitle: string;
  items: { bucket: string; weighted_score: number }[];
  render: (bucket: string) => string;
  tone: "accent" | "muted";
}) {
  // Order by weighted score (descending) so the most-mentioned items
  // come first — but we never expose the score itself in the UI.
  const ordered = [...items].sort(
    (a, b) => (b.weighted_score ?? 0) - (a.weighted_score ?? 0),
  );
  const cap = 5;
  const visible = ordered.slice(0, cap);
  return (
    <div className="rounded-md border border-border bg-surface p-6">
      <header className="mb-3 space-y-1">
        <h3 className="text-lg font-semibold text-text-primary">
          {title}
        </h3>
        <p className="text-xs text-text-muted">{subtitle}</p>
      </header>
      {visible.length === 0 ? (
        <p className="text-sm text-text-muted">
          The simulation didn&apos;t surface enough signal for this
          section to be useful — usually a sign that more retrieval
          coverage is needed.
        </p>
      ) : (
        <ol className="space-y-2.5">
          {visible.map((b, i) => (
            <li
              key={b.bucket}
              className="flex items-start gap-3 rounded-md border border-border bg-surface-elevated px-3 py-2.5 text-sm leading-snug"
            >
              <span
                className={`mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-mono ${
                  tone === "accent"
                    ? "border-accent-border text-accent"
                    : "border-border text-text-muted"
                }`}
                aria-hidden
              >
                {i + 1}
              </span>
              <span className="text-text-body">{render(b.bucket)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
