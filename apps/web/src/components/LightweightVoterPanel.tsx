"use client";
// Phase 14A — 100-voter influence layer panel.
//
// Surfaces the lightweight voter overlay that already runs on every
// simulation but was previously invisible to founders. Honors the
// product principle:
//
//   "Debate agents talk; voters absorb and spread."
//
// The 24-ish deep agents are the ones generating arguments (visible in
// the Debate Transcript). The 100 voters are a larger simulated sample
// drawn from the same evidence; they react to the deep-agent
// arguments and propagate them through a 100-node bounded-confidence
// influence network over 4 rounds. No LLM calls per voter; no free-
// text generation.
//
// Empty-state policy: if the run pre-dates Phase 12C or the overlay
// artifacts are missing, this component renders nothing. The
// surrounding report continues to render normally.

import { useMemo, useState } from "react";

import type {
  LightweightVotersPayload,
  VoterBucketDistribution,
  VoterInfluenceRound,
} from "@/lib/types";

export interface LightweightVoterPanelProps {
  payload: LightweightVotersPayload | null | undefined;
  isLoading?: boolean;
  /** Surface the actual fetch error so the user understands why the
   *  panel can't render (transient API failure, CORS, etc.) instead
   *  of silently hiding the feature. */
  fetchError?: Error | null;
}

const BUCKET_LABELS: Record<keyof VoterBucketDistribution, string> = {
  buyer: "Buyer",
  receptive: "Receptive",
  uncertain: "Uncertain",
  skeptical: "Skeptical",
  total_population_weight: "",
  n_voters: "",
};

const BUCKET_ORDER = ["buyer", "receptive", "uncertain", "skeptical"] as const;

const BUCKET_TONE: Record<(typeof BUCKET_ORDER)[number], string> = {
  buyer: "bg-accent",
  receptive: "bg-accent/60",
  uncertain: "bg-text-muted/60",
  skeptical: "bg-danger/70",
};

const BUCKET_TONE_TEXT: Record<(typeof BUCKET_ORDER)[number], string> = {
  buyer: "text-accent",
  receptive: "text-accent",
  uncertain: "text-text-muted",
  skeptical: "text-danger",
};

function pct(x: number | undefined | null, digits = 0): string {
  if (x == null || Number.isNaN(x)) return "—";
  return `${x.toFixed(digits)}%`;
}

/**
 * Compact panel shell used for loading / error / unavailable states.
 * Always emits a visible element so the user knows the 100-voter
 * feature exists even when the data isn't there. Keeps the panel
 * placement honest — never silently drops the section.
 */
function VoterPanelShell({
  testid,
  eyebrow,
  title,
  body,
  detail,
}: {
  testid: string;
  eyebrow: string;
  title: string;
  body: string;
  detail?: string;
}) {
  return (
    <section
      data-testid={testid}
      className="space-y-2 rounded-md border border-border bg-surface p-5 text-sm"
    >
      <p className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-text-muted">
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rotate-45 bg-text-muted/70"
        />
        {eyebrow}
      </p>
      <h3 className="text-base font-semibold text-text-primary">
        {title}
      </h3>
      <p className="text-text-body">{body}</p>
      {detail ? (
        <p
          data-testid="voter-panel-detail"
          className="font-mono text-[11px] text-text-muted"
        >
          {detail}
        </p>
      ) : null}
    </section>
  );
}

export function LightweightVoterPanel({
  payload,
  isLoading,
  fetchError,
}: LightweightVoterPanelProps) {
  const [showDynamics, setShowDynamics] = useState(false);

  // Visible state for every code path. We deliberately never return
  // null silently — that's what hid the 100-voter feature in the
  // ShelfSense AI run report. The user must always see WHY the
  // panel can or can't render.

  if (isLoading) {
    return (
      <VoterPanelShell
        testid="lightweight-voter-panel-loading"
        eyebrow="100-voter influence layer"
        title="Loading 100-voter overlay…"
        body="The voter influence layer is loading. This usually takes under a second on a completed run."
      />
    );
  }

  if (fetchError) {
    return (
      <VoterPanelShell
        testid="lightweight-voter-panel-error"
        eyebrow="100-voter influence layer"
        title="Voter overlay could not be loaded for this run."
        body={
          "The 100-voter influence-loop ran during the simulation; " +
          "fetching its artifact from the API just failed. Reload the " +
          "page to retry, or try again in a minute — if it keeps " +
          "happening, the run's voter artifact may be temporarily " +
          "unavailable on the server."
        }
        detail={fetchError.message}
      />
    );
  }

  if (!payload || !payload.voter_overlay_available) {
    return (
      <VoterPanelShell
        testid="lightweight-voter-panel-unavailable"
        eyebrow="100-voter influence layer"
        title="100-voter influence layer unavailable for this run."
        body={
          "This can happen for older runs from before the voter " +
          "overlay shipped, or when the voter artifact is missing " +
          "from the server. New simulations show the 100-voter graph " +
          "automatically. The rest of the report below is unaffected."
        }
        detail={
          payload && "reason" in payload && typeof payload.reason === "string"
            ? payload.reason
            : undefined
        }
      />
    );
  }

  const dist = payload.final_distribution ?? null;
  const voterCount = payload.voters_count ?? dist?.n_voters ?? 100;
  const cal = payload.calibrated_distribution ?? null;
  const rounds = (payload.influence_rounds ?? []).slice().sort(
    (a, b) => a.round_idx - b.round_idx,
  );
  const totalShifts = rounds.reduce(
    (acc, r) => acc + (r.bucket_changes ?? 0), 0,
  );
  const cluster = payload.cluster_arguments ?? null;

  return (
    <section
      data-testid="lightweight-voter-panel"
      className="space-y-5 rounded-md border border-border bg-surface p-6"
    >
      <header className="space-y-1">
        <p className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-text-muted">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rotate-45 bg-accent shadow-[0_0_6px_rgba(170,255,0,0.55)]"
          />
          {voterCount}-voter influence layer
        </p>
        <h3 className="text-xl font-semibold text-text-primary">
          A larger simulated sample that absorbs and spreads the debate
          signal.
        </h3>
      </header>

      {/* Primary chart — 4-bucket distribution */}
      <BucketDistributionChart
        distribution={dist}
        voterCount={voterCount}
      />

      {/* Quick stats row */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat
          value={voterCount}
          label="Voters in this run"
        />
        <Stat
          value={totalShifts}
          label="Bucket-level shifts across 4 rounds"
        />
        <Stat
          value={cal?.confidence_band_pp != null
            ? `±${cal.confidence_band_pp.toFixed(0)} pp`
            : "—"}
          label="Confidence band (4-bucket)"
        />
      </div>

      {/* Cluster-argument highlights */}
      {cluster && (cluster.pro || cluster.con) ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {Array.isArray(cluster.pro) && cluster.pro.length > 0 ? (
            <ArgumentList
              title="Strongest spreading arguments"
              items={cluster.pro.slice(0, 3)}
              tone="accent"
            />
          ) : null}
          {Array.isArray(cluster.con) && cluster.con.length > 0 ? (
            <ArgumentList
              title="Most resisted arguments"
              items={cluster.con.slice(0, 3)}
              tone="danger"
            />
          ) : null}
        </div>
      ) : null}

      {/* "How the 100 voters work" copy block */}
      <HowVotersWork />

      {/* Influence dynamics — optional, collapsed by default */}
      {rounds.length > 0 ? (
        <div className="space-y-2">
          <button
            type="button"
            data-testid="voter-dynamics-toggle"
            onClick={() => setShowDynamics((v) => !v)}
            className="text-xs uppercase tracking-wider text-text-muted hover:text-accent"
          >
            {showDynamics ? "Hide" : "Show"} influence dynamics
          </button>
          {showDynamics ? (
            <InfluenceDynamics rounds={rounds} voterCount={voterCount} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// ----------------------------------------------------------------------

function BucketDistributionChart({
  distribution,
  voterCount,
}: {
  distribution: VoterBucketDistribution | null;
  voterCount: number;
}) {
  const bars = useMemo(() => {
    if (!distribution) return [];
    return BUCKET_ORDER.map((bucket) => {
      const value =
        (distribution[bucket as keyof VoterBucketDistribution] as
          | number
          | undefined) ?? 0;
      return {
        bucket,
        label: BUCKET_LABELS[bucket],
        pct: value,
        count: Math.round((value / 100) * voterCount),
      };
    });
  }, [distribution, voterCount]);

  if (bars.length === 0) {
    return (
      <p
        data-testid="voter-distribution-empty"
        className="text-sm text-text-muted"
      >
        Voter distribution not available for this run.
      </p>
    );
  }

  return (
    <div
      data-testid="voter-distribution-chart"
      className="space-y-3"
    >
      {bars.map((bar) => (
        <div key={bar.bucket} className="space-y-1">
          <div className="flex items-baseline justify-between gap-3">
            <span
              className={`text-sm font-medium ${BUCKET_TONE_TEXT[bar.bucket]}`}
            >
              {bar.label}
            </span>
            <span className="font-mono text-sm text-text-primary">
              {bar.count}/{voterCount}{" "}
              <span className="text-text-muted">({pct(bar.pct, 0)})</span>
            </span>
          </div>
          <div
            className="h-2 w-full overflow-hidden rounded-sm bg-border"
            aria-hidden
          >
            <span
              style={{ width: `${Math.max(0, Math.min(100, bar.pct))}%` }}
              className={`block h-full ${BUCKET_TONE[bar.bucket]}`}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function Stat({
  value,
  label,
}: {
  value: number | string;
  label: string;
}) {
  return (
    <div className="rounded-md border border-border bg-surface-elevated p-3">
      <p className="font-mono text-2xl text-accent">{value}</p>
      <p className="mt-0.5 text-[11px] uppercase tracking-wider text-text-muted">
        {label}
      </p>
    </div>
  );
}

function ArgumentList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "accent" | "danger";
}) {
  const toneClass =
    tone === "danger" ? "text-danger" : "text-accent";
  return (
    <div className="rounded-md border border-border bg-surface-elevated p-3 text-sm">
      <p className={`mb-2 text-xs uppercase tracking-wider ${toneClass}`}>
        {title}
      </p>
      <ul className="space-y-1.5">
        {items.map((it, i) => (
          <li key={i} className="text-text-body">
            — {String(it).slice(0, 240)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function HowVotersWork() {
  return (
    <details
      data-testid="how-voters-work"
      className="rounded-md border border-border bg-surface-elevated p-4 text-sm"
      open
    >
      <summary className="cursor-pointer text-xs font-medium uppercase tracking-wider text-text-muted">
        How the 100 voters work
      </summary>
      <div className="mt-3 space-y-2 text-text-body">
        <p>
          The personas in the <strong>Debate transcript</strong> are
          the ones doing the talking. They are full LLM agents that
          argue, push back, and revise their views across 4 groups and
          4 rounds.
        </p>
        <p>
          The 100 voters are a <strong>larger simulated sample</strong>{" "}
          drawn from the same evidence and cohorts. They do not write
          new messages. Instead, they react to the arguments the
          debate agents made and propagate those arguments through a
          100-voter influence network over 4 rounds.
        </p>
        <p className="text-text-muted">
          In short: <span className="text-text-primary">debate agents
          talk; voters absorb and spread.</span>
        </p>
      </div>
    </details>
  );
}

function InfluenceDynamics({
  rounds,
  voterCount,
}: {
  rounds: VoterInfluenceRound[];
  voterCount: number;
}) {
  return (
    <div
      data-testid="voter-influence-dynamics"
      className="space-y-3 rounded-md border border-border bg-surface-elevated p-3 text-xs"
    >
      <p className="text-[11px] uppercase tracking-wider text-text-muted">
        Bucket distribution across 4 influence rounds
      </p>
      {rounds.map((r) => {
        const bd = (r.bucket_distribution ?? {}) as Record<string, number>;
        const total = BUCKET_ORDER.reduce(
          (acc, b) => acc + (bd[b] ?? 0), 0,
        );
        const denom = total > 0 ? total : voterCount;
        return (
          <div
            key={r.round_idx}
            data-testid={`voter-round-${r.round_idx}`}
            className="space-y-1"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-text-primary">
                Round {r.round_idx}
              </span>
              <span className="text-text-muted">
                {r.intent_changes ?? 0} intent shifts
                {r.bucket_changes != null
                  ? ` · ${r.bucket_changes} bucket changes`
                  : null}
              </span>
            </div>
            <div
              className="flex h-2 w-full overflow-hidden rounded-sm bg-border"
              aria-hidden
            >
              {BUCKET_ORDER.map((b) => {
                const count = bd[b] ?? 0;
                const pctWidth =
                  denom > 0 ? (count / denom) * 100 : 0;
                return pctWidth > 0 ? (
                  <span
                    key={b}
                    style={{ width: `${pctWidth}%` }}
                    className={`block h-full ${BUCKET_TONE[b]}`}
                  />
                ) : null;
              })}
            </div>
          </div>
        );
      })}
      <p className="pt-1 text-text-muted">
        Round 0 = baseline; rounds 1–3 propagate the debate
        arguments through the voter network.
      </p>
    </div>
  );
}
