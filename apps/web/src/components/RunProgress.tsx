"use client";
// Phase 10B — run progress screen with polling. Shows founder-friendly
// stage names, completed/active/failed states, and a glowing accent
// line for the active stage. Never shows fake reports on failure.

import { useEffect, useMemo, useState } from "react";
import { getAssemblyRun } from "@/lib/api";
import type { RunStatusResponse } from "@/lib/types";

const FOUNDER_FRIENDLY_LABELS: Record<string, string> = {
  validating_brief: "Validating brief",
  planning_evidence: "Planning evidence",
  retrieving_evidence: "Retrieving market evidence",
  scoring_evidence: "Scoring evidence",
  building_personas: "Building synthetic society",
  enriching_psychology: "Adding psychology traits",
  running_individual_simulation: "Pre-discussion stance",
  running_group_discussion: "Running group discussion",
  repairing_incomplete_outputs: "Repairing incomplete outputs",
  building_cohorts: "Building cohorts",
  inferring_simulated_intent: "Inferring simulated intent",
  running_society_wide_debate: "Running society-wide debate",
  generating_report: "Generating report",
};

const ORDERED_STAGES = [
  "validating_brief",
  "planning_evidence",
  "retrieving_evidence",
  "scoring_evidence",
  "building_personas",
  "enriching_psychology",
  "running_individual_simulation",
  "running_group_discussion",
  "repairing_incomplete_outputs",
  "building_cohorts",
  "inferring_simulated_intent",
  "running_society_wide_debate",
  "generating_report",
] as const;

export interface RunProgressProps {
  runId: string;
  /** Polling interval in ms. Default 5000 — keep it gentle. */
  pollIntervalMs?: number;
  onComplete?: (status: RunStatusResponse) => void;
  /**
   * When true (the default), the progress card returns null once the
   * run reaches `complete` so the founder can focus on the cockpit
   * + report. Failed runs always render so the failure cause stays
   * visible.
   */
  hideOnComplete?: boolean;
}

export function RunProgress({
  runId,
  pollIntervalMs = 5000,
  onComplete,
  hideOnComplete = true,
}: RunProgressProps) {
  const [status, setStatus] = useState<RunStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const s = await getAssemblyRun(runId);
        if (cancelled) return;
        setStatus(s);
        setError(null);
        if (s.status === "complete") {
          onComplete?.(s);
          return;
        }
        if (s.status === "failed") {
          return;
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Unknown error");
      }
      if (!cancelled) {
        timer = setTimeout(tick, pollIntervalMs);
      }
    }
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, pollIntervalMs, onComplete]);

  const orderedRows = useMemo(() => {
    return ORDERED_STAGES.map((stage) => {
      const info = status?.stage_progress?.[stage];
      const stageStatus = info?.status ?? "pending";
      return {
        stage,
        label: FOUNDER_FRIENDLY_LABELS[stage] ?? stage,
        stageStatus,
        startedAt: info?.started_at ?? null,
        completedAt: info?.completed_at ?? null,
      };
    });
  }, [status]);

  // Auto-hide once the run completes so the cockpit/report can take
  // over the page. Failed runs always render so the user can see why.
  if (hideOnComplete && status?.status === "complete") {
    return null;
  }

  return (
    <section
      data-testid="run-progress"
      className="space-y-6 rounded-md border border-border bg-surface p-6"
    >
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-text-primary">
            Synthetic society in progress
          </h2>
          <p className="text-sm text-text-muted">
            Live retrieval, persona generation, and a 7-round
            discussion. Typically 12–20 minutes.
          </p>
        </div>
        <ProgressBadge status={status} />
      </header>

      {error ? (
        <div
          role="alert"
          className="rounded-md border border-danger/40 bg-surface-elevated px-4 py-3 text-sm text-danger"
        >
          {error}
        </div>
      ) : null}

      {status?.status === "failed" ? (
        <FailedCard status={status} />
      ) : null}

      <ol className="space-y-2">
        {orderedRows.map((r) => (
          <StageRow key={r.stage} {...r} />
        ))}
      </ol>

      {/* Phase 14A — surface the 100-voter influence loop without
          changing any backend stage names (no Alembic migration, no
          new ck_assembly_runs_current_stage value). The voter
          overlay runs inside the existing intent-inference stage
          and is failure-tolerant; this note is purely informational
          so founders see that the 100-voter layer exists. */}
      <p
        data-testid="voter-overlay-progress-note"
        className="rounded-md border border-border bg-surface-elevated px-3 py-2 text-xs text-text-muted"
      >
        <span className="font-medium text-text-primary">
          Running 100-voter influence loop
        </span>{" "}
        — runs in parallel with intent inference. 100 lightweight
        voters propagate the strongest debate arguments through a
        4-round influence network. No new LLM calls; the deep agents
        do the talking.
      </p>

      <p className="text-xs text-text-muted">
        Live runs typically complete in 12–20 minutes. Status refreshes every{" "}
        {Math.round(pollIntervalMs / 1000)}s.
      </p>
    </section>
  );
}

function ProgressBadge({ status }: { status: RunStatusResponse | null }) {
  if (!status) {
    return (
      <span className="rounded border border-border px-2 py-1 text-xs text-text-muted">
        connecting…
      </span>
    );
  }
  if (status.status === "complete") {
    return (
      <span className="rounded border border-accent-border bg-accent-soft px-2 py-1 text-xs font-medium text-accent">
        complete · {status.progress_pct}%
      </span>
    );
  }
  if (status.status === "failed") {
    return (
      <span className="rounded border border-danger/40 px-2 py-1 text-xs font-medium text-danger">
        failed @ {status.failed_stage ?? status.current_stage}
      </span>
    );
  }
  return (
    <span className="rounded border border-accent-border px-2 py-1 text-xs font-medium text-accent">
      running · {status.progress_pct}%
    </span>
  );
}

function StageRow({
  label,
  stageStatus,
}: {
  stage: string;
  label: string;
  stageStatus: string;
  startedAt: string | null;
  completedAt: string | null;
}) {
  const isComplete = stageStatus === "complete";
  const isRunning = stageStatus === "running";
  const isFailed = stageStatus === "failed";
  const isSkipped = stageStatus === "skipped";
  return (
    <li
      data-testid="run-progress-stage"
      data-stage-status={stageStatus}
      className={`flex items-center gap-3 rounded-md border px-3 py-2.5 text-sm ${
        isRunning
          ? "border-accent-border bg-accent-soft accent-glow"
          : isFailed
            ? "border-danger/40 bg-surface-elevated"
            : isComplete
              ? "border-border bg-surface-elevated"
              : "border-border/60"
      }`}
    >
      <StageMarker
        isComplete={isComplete}
        isRunning={isRunning}
        isFailed={isFailed}
        isSkipped={isSkipped}
      />
      <span
        className={`flex-1 ${isComplete ? "text-text-primary" : isFailed ? "text-danger" : isRunning ? "text-accent" : "text-text-muted"}`}
      >
        {label}
      </span>
      <span className="font-mono text-xs text-text-muted">
        {stageStatus}
      </span>
    </li>
  );
}

function StageMarker({
  isComplete,
  isRunning,
  isFailed,
  isSkipped,
}: {
  isComplete: boolean;
  isRunning: boolean;
  isFailed: boolean;
  isSkipped: boolean;
}) {
  if (isComplete)
    return (
      <span
        aria-hidden
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-accent-border bg-accent-soft text-[10px] font-bold text-accent"
      >
        ✓
      </span>
    );
  if (isFailed)
    return (
      <span
        aria-hidden
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-danger/40 text-[10px] font-bold text-danger"
      >
        ×
      </span>
    );
  if (isRunning)
    return (
      <span
        aria-hidden
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-accent-border"
      >
        <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
      </span>
    );
  if (isSkipped)
    return (
      <span
        aria-hidden
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-border text-[10px] font-bold text-text-muted"
      >
        –
      </span>
    );
  return (
    <span
      aria-hidden
      className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-border"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-text-muted" />
    </span>
  );
}

function FailedCard({ status }: { status: RunStatusResponse }) {
  return (
    <div
      data-testid="run-failed-card"
      className="space-y-2 rounded-md border border-danger/40 bg-surface-elevated p-4"
    >
      <div className="flex items-center gap-2">
        <span className="inline-block h-2 w-2 rounded-full bg-danger" />
        <span className="font-medium text-danger">
          Run failed at: {status.failed_stage ?? status.current_stage}
        </span>
      </div>
      <p
        className="whitespace-pre-wrap text-sm text-text-body"
        data-testid="run-failed-message"
      >
        {status.error_message ??
          "The run failed but no error message was provided. Try a fresh run."}
      </p>
      <p className="text-xs text-text-muted">
        Synthetic society aborted safely. No partial / fake report was
        produced.
      </p>
    </div>
  );
}
