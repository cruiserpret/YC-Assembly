"use client";
// Phase 10B+ — outcome stats panel.
//
// Computes how many synthetic personas SHIFTED their stance bucket
// between the pre-discussion ballot and the final ballot, and how
// many HELD. Renders a picture-1-style panel: big number + label,
// big number + label, then a progress bar at the bottom showing
// the opinion-shift rate as a percentage.

import { useMemo } from "react";
import { bucketStance } from "@/lib/stance";
import type { DiscussionTranscriptPayload } from "@/lib/types";

export interface OutcomeStatsProps {
  transcript: DiscussionTranscriptPayload;
}

export function OutcomeStats({ transcript }: OutcomeStatsProps) {
  const stats = useMemo(() => {
    let shifted = 0;
    let held = 0;
    let totalScored = 0;
    for (const [, b] of Object.entries(transcript.private_ballots)) {
      const pre = b.pre?.stance ?? null;
      const final = b.final?.stance ?? b.reflection?.stance ?? null;
      if (!pre || !final) continue;
      totalScored += 1;
      if (bucketStance(pre) !== bucketStance(final)) {
        shifted += 1;
      } else {
        held += 1;
      }
    }
    const shiftRate = totalScored > 0 ? shifted / totalScored : 0;
    return { shifted, held, totalScored, shiftRate };
  }, [transcript]);

  return (
    <section
      data-testid="outcome-stats"
      className="space-y-5 rounded-md border border-border bg-surface p-6"
    >
      <header>
        <h3 className="text-xs uppercase tracking-wider text-text-muted">
          Outcome stats
        </h3>
      </header>

      <Stat
        value={stats.shifted}
        label="Agents shifted"
        accent
      />
      <div className="border-t border-border" />
      <Stat
        value={stats.held}
        label="Agents held"
      />
      <div className="border-t border-border" />

      <div className="space-y-2">
        <div
          className="h-2 w-full overflow-hidden rounded-sm bg-border"
          aria-hidden
          data-testid="outcome-shift-bar"
        >
          <span
            className="block h-full bg-accent"
            style={{ width: `${(stats.shiftRate * 100).toFixed(0)}%` }}
          />
        </div>
        <p className="text-xs text-text-muted">
          <span className="font-mono text-text-body">
            {(stats.shiftRate * 100).toFixed(0)}%
          </span>{" "}
          opinion shift rate
        </p>
      </div>
    </section>
  );
}

function Stat({
  value,
  label,
  accent,
}: {
  value: number;
  label: string;
  accent?: boolean;
}) {
  return (
    <div>
      <p
        className={`font-mono text-5xl leading-none ${accent ? "text-accent" : "text-text-muted"}`}
      >
        {value}
      </p>
      <p className="mt-2 text-xs uppercase tracking-wider text-text-muted">
        {label}
      </p>
    </div>
  );
}
