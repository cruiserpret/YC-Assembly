"use client";
// Phase 10B+ — God's-eye-view header card.
//
// Sits at the top of the run page. Shows the big product title,
// kicker, the founder report's executive summary, a "Where this
// synthetic discussion landed" trajectory block, and a final
// consensus chart with FOR/AGAINST split + a big shift-rate
// percentage. Visual is inspired by lab.assemblysimulator.com but
// retuned to Assembly's locked palette and observation-only
// language (no real-world forecasts, no launch verdicts).

import { useMemo } from "react";
import { bucketStance } from "@/lib/stance";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
} from "@/lib/types";

export interface GodsEyeHeaderProps {
  report: FounderReport;
  transcript: DiscussionTranscriptPayload;
}

export function GodsEyeHeader({ report, transcript }: GodsEyeHeaderProps) {
  const productName =
    (report.product_brief?.product_name as string | undefined) ??
    "your product";

  const stats = useMemo(() => {
    let shifted = 0;
    let scored = 0;
    let forCount = 0;
    let againstCount = 0;
    let neutralCount = 0;
    for (const [, b] of Object.entries(transcript.private_ballots)) {
      const pre = b.pre?.stance ?? null;
      const final = b.final?.stance ?? b.reflection?.stance ?? null;
      if (final) {
        const bucket = bucketStance(final);
        if (bucket === "for") forCount += 1;
        else if (bucket === "against") againstCount += 1;
        else neutralCount += 1;
      }
      if (pre && final) {
        scored += 1;
        if (bucketStance(pre) !== bucketStance(final)) shifted += 1;
      }
    }
    const total = forCount + againstCount + neutralCount;
    const shiftRate = scored > 0 ? shifted / scored : 0;
    return {
      shifted,
      scored,
      shiftRate,
      forCount,
      againstCount,
      neutralCount,
      total,
    };
  }, [transcript]);

  const cohortCount = report.cohort_count ?? 0;
  const date = useMemo(() => {
    const d = new Date();
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  }, []);

  // Synthesize the trajectory sentence from the actual stance
  // distribution + shift rate. We deliberately do NOT pull from the
  // LLM's executive_summary bullets here because those tend to read
  // like raw stats (`{would_consider: 19, would_reject: 1}`) rather
  // than natural-language outcomes. Building the sentence ourselves
  // lets the founder read the trajectory in plain English.
  const trajectoryLine = useMemo(
    () =>
      synthesizeTrajectory({
        forCount: stats.forCount,
        againstCount: stats.againstCount,
        neutralCount: stats.neutralCount,
        total: stats.total,
        shifted: stats.shifted,
        scored: stats.scored,
      }),
    [stats],
  );

  return (
    <section
      data-testid="gods-eye-header"
      className="relative overflow-hidden rounded-md border border-border bg-surface"
    >
      {/* faint grid backdrop for the lab feel */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "linear-gradient(to right, var(--text-muted) 1px, transparent 1px), linear-gradient(to bottom, var(--text-muted) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
      />
      <div className="relative space-y-8 p-8">
        <header className="space-y-4">
          <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.2em] text-accent">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rounded-full bg-accent"
            />
            Meta report
          </p>
          <h1 className="text-4xl font-extrabold uppercase tracking-tight text-text-primary sm:text-5xl lg:text-6xl">
            {productName}
          </h1>
          <p className="font-mono text-xs text-text-muted">
            {cohortCount} cohort{cohortCount === 1 ? "" : "s"}{" "}
            <span className="mx-2">·</span>
            {date}
          </p>
        </header>

        {/* Trajectory observation — natural-language sentence
            synthesized from the discussion data. */}
        <div>
          <p className="mb-3 font-mono text-xs uppercase tracking-wider text-text-muted">
            Where the discussion landed
          </p>
          <div className="border-l-2 border-accent bg-surface-elevated/60 px-5 py-4">
            <p className="flex items-start gap-3 text-base leading-relaxed text-text-body">
              <span
                aria-hidden
                className="mt-1 inline-block w-3 shrink-0 text-accent"
              >
                →
              </span>
              <span>{trajectoryLine}</span>
            </p>
          </div>
        </div>

        {/* Final consensus chart */}
        <div>
          <p className="mb-3 font-mono text-xs uppercase tracking-wider text-text-muted">
            Final consensus
          </p>
          <div className="grid grid-cols-1 gap-6 rounded-md border border-border bg-surface-elevated p-6 lg:grid-cols-[1.6fr_1fr]">
            <div className="space-y-3">
              <ConsensusBar
                label="Receptive"
                count={stats.forCount}
                total={stats.total}
                tone="accent"
              />
              <ConsensusBar
                label="Uncertain"
                count={stats.neutralCount}
                total={stats.total}
                tone="muted"
              />
              <ConsensusBar
                label="Resistant"
                count={stats.againstCount}
                total={stats.total}
                tone="danger"
              />
            </div>
            <div className="border-t border-border pt-4 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
              <p className="font-mono text-5xl font-bold text-accent">
                {(stats.shiftRate * 100).toFixed(0)}%
              </p>
              <p className="mt-1 font-mono text-xs text-text-muted">
                opinion shift rate across all agents
              </p>
              <p className="mt-3 text-xs leading-relaxed text-text-body">
                <span className="font-mono text-text-primary">
                  {stats.shifted}
                </span>{" "}
                of{" "}
                <span className="font-mono text-text-primary">
                  {stats.scored}
                </span>{" "}
                agents revised their position during the simulation.
                {stats.shiftRate > 0
                  ? " The discussion produced partial movement toward a shared position."
                  : " No agents changed stance during the discussion."}
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/** Synthesize a natural-language trajectory sentence from the
 *  observed stance distribution + shift rate. We deliberately keep
 *  this observation-only (no real-world forecast claim). */
function synthesizeTrajectory(stats: {
  forCount: number;
  againstCount: number;
  neutralCount: number;
  total: number;
  shifted: number;
  scored: number;
}): string {
  const { forCount, againstCount, neutralCount, total, shifted, scored } =
    stats;
  if (total === 0) {
    return (
      "The synthetic society finished with no recorded final stances " +
      "for this run."
    );
  }
  const forPct = forCount / total;
  const againstPct = againstCount / total;
  const neutralPct = neutralCount / total;
  const shiftPct = scored > 0 ? shifted / scored : 0;

  // Lean
  let lean: string;
  if (forPct >= 0.6) {
    lean = `${pct(forPct)} of the synthetic society finished receptive by the end`;
  } else if (forPct >= 0.45 && forPct > againstPct) {
    lean = `the synthetic society leaned receptive, with ${forCount} of ${total} personas finishing supportive`;
  } else if (againstPct >= 0.4) {
    lean = `the synthetic society leaned skeptical, with ${againstCount} of ${total} personas resisting`;
  } else if (neutralPct >= 0.5) {
    lean = `most of the synthetic society stayed uncertain — ${neutralCount} of ${total} personas finished still curious or wanting more information`;
  } else {
    lean = `the room split — ${forCount} receptive, ${neutralCount} uncertain, ${againstCount} resistant`;
  }

  // Shift commentary
  let shiftPhrase: string;
  if (scored === 0) {
    shiftPhrase = "stance shift was not measurable on this run";
  } else if (shiftPct >= 0.4) {
    shiftPhrase = `the discussion materially moved ${shifted} personas (${pct(shiftPct)} shift rate)`;
  } else if (shiftPct >= 0.15) {
    shiftPhrase = `${shifted} personas shifted position during the discussion (${pct(shiftPct)} shift rate)`;
  } else if (shiftPct > 0) {
    shiftPhrase = `stances mostly held — only ${shifted} personas shifted (${pct(shiftPct)} shift rate)`;
  } else {
    shiftPhrase = "no personas changed bucket during the discussion";
  }

  // Plain-English trajectory framing — observation, not a real-world
  // forecast. Caveat is mandatory.
  let trajectory: string;
  if (forPct >= 0.55 && shiftPct >= 0.2) {
    trajectory =
      "synthetic trajectory: receptive, with the debate strengthening interest";
  } else if (forPct >= 0.55) {
    trajectory =
      "synthetic trajectory: receptive room from the start, with stances mostly intact";
  } else if (againstPct >= 0.4) {
    trajectory =
      "synthetic trajectory: contested — a sizeable group resists and would need targeted proof to move";
  } else if (neutralPct >= 0.5) {
    trajectory =
      "synthetic trajectory: undecided — the simulation suggests proof points are the bottleneck, not interest";
  } else {
    trajectory =
      "synthetic trajectory: mixed — no clear majority emerged from the synthetic discussion";
  }

  return `${capitalize(lean)}; ${shiftPhrase}. ${capitalize(trajectory)} — this is a synthetic signal, not a real-world purchase forecast, and should be validated with real prospects.`;
}

function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}

function capitalize(s: string): string {
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function ConsensusBar({
  label,
  count,
  total,
  tone,
}: {
  label: string;
  count: number;
  total: number;
  tone: "accent" | "danger" | "muted";
}) {
  const pct = total > 0 ? (count / total) * 100 : 0;
  const bar =
    tone === "accent"
      ? "bg-accent"
      : tone === "danger"
        ? "bg-danger"
        : "bg-text-muted";
  const text =
    tone === "accent"
      ? "text-accent"
      : tone === "danger"
        ? "text-danger"
        : "text-text-muted";
  return (
    <div className="grid grid-cols-[64px_1fr_32px] items-center gap-3">
      <span
        className={`font-mono text-xs uppercase tracking-wider ${text}`}
      >
        {label}
      </span>
      <span
        className="h-2 overflow-hidden rounded-sm bg-border"
        aria-hidden
      >
        <span
          className={`block h-full ${bar}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-right font-mono text-sm text-text-body">
        {count}
      </span>
    </div>
  );
}
