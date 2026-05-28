"use client";
// Phase 14B — 100-voter influence graph.
//
// A separate visual surface from the 24-agent deep-debate graph.
// Renders 100 small dots in a deterministic packed grid, colored by
// the voter's final bucket (buyer / receptive / uncertain /
// skeptical). The grid is intentionally NOT a force-directed layout —
// these are voters absorbing the debate signal, not agents arguing
// with each other, so a calm uniform grid honors the architecture.
//
// Honesty rules baked in:
//   - exactly 100 dots when voters_count == 100
//   - dot count derived from final_distribution bucket totals, NOT
//     inferred from the percentage; if the API sends 24/22/0/54 we
//     show 24+22+0+54 = 100 dots
//   - color order matches the bucket distribution chart so the
//     surrounding panel reads as one unit
//   - "Debate agents talk. 100 voters absorb and spread." copy is
//     part of this component, not a tooltip
//
// No LLM calls, no canvas (static SVG so it embeds cleanly in PDF
// downloads later if needed).

import { useMemo } from "react";

import type { VoterBucketDistribution } from "@/lib/types";

export interface VoterInfluenceGraphProps {
  distribution: VoterBucketDistribution | null | undefined;
  voterCount?: number;
}

const BUCKET_ORDER = ["buyer", "receptive", "uncertain", "skeptical"] as const;

type BucketKey = (typeof BUCKET_ORDER)[number];

const BUCKET_DOT_FILL: Record<BucketKey, string> = {
  buyer: "#a7f538",      // accent-bright
  receptive: "#66c91e",  // accent-mid
  uncertain: "#737373",  // text-muted
  skeptical: "#ef4444",  // danger
};

const BUCKET_LABEL: Record<BucketKey, string> = {
  buyer: "Buyer",
  receptive: "Receptive",
  uncertain: "Uncertain",
  skeptical: "Skeptical",
};

function bucketCountsFromDistribution(
  distribution: VoterBucketDistribution | null | undefined,
  voterCount: number,
): Record<BucketKey, number> {
  if (!distribution) {
    return { buyer: 0, receptive: 0, uncertain: 0, skeptical: 0 };
  }
  // Convert percentages to whole-dot counts. We round each, then
  // fix any rounding drift so the four counts sum to voterCount
  // exactly. This guarantees 100 dots when voterCount is 100.
  const raw = BUCKET_ORDER.map((b) => {
    const pct = (distribution[b] as number | undefined) ?? 0;
    return { bucket: b, exact: (pct / 100) * voterCount };
  });
  const floored = raw.map((r) => ({
    ...r,
    floor: Math.floor(r.exact),
    frac: r.exact - Math.floor(r.exact),
  }));
  const flooredSum = floored.reduce((acc, r) => acc + r.floor, 0);
  const remainder = Math.max(0, voterCount - flooredSum);
  // Distribute the remainder to buckets with the largest fractional
  // parts (Hamilton method) so the rounding is honest.
  const sortedByFrac = floored
    .slice()
    .sort((a, b) => b.frac - a.frac);
  const result: Record<BucketKey, number> = {
    buyer: 0, receptive: 0, uncertain: 0, skeptical: 0,
  };
  for (const r of floored) result[r.bucket] = r.floor;
  for (let i = 0; i < remainder && i < sortedByFrac.length; i += 1) {
    result[sortedByFrac[i].bucket] += 1;
  }
  return result;
}

export function VoterInfluenceGraph({
  distribution,
  voterCount = 100,
}: VoterInfluenceGraphProps) {
  // Compute integer per-bucket counts that sum to voterCount.
  const bucketCounts = useMemo(
    () => bucketCountsFromDistribution(distribution, voterCount),
    [distribution, voterCount],
  );

  // Build the dot list in bucket order. Each dot inherits the bucket
  // color. We layout in a 10×10 grid for 100 voters; falls through
  // for other voter counts to ceil(sqrt(n)).
  const dots = useMemo(() => {
    const total = BUCKET_ORDER.reduce(
      (acc, b) => acc + bucketCounts[b], 0,
    );
    const cols = Math.ceil(Math.sqrt(Math.max(1, total)));
    const out: Array<{
      x: number;
      y: number;
      fill: string;
      bucket: BucketKey;
      index: number;
    }> = [];
    let idx = 0;
    for (const bucket of BUCKET_ORDER) {
      const n = bucketCounts[bucket];
      for (let i = 0; i < n; i += 1) {
        const col = idx % cols;
        const row = Math.floor(idx / cols);
        out.push({
          x: col,
          y: row,
          fill: BUCKET_DOT_FILL[bucket],
          bucket,
          index: idx,
        });
        idx += 1;
      }
    }
    return { dots: out, cols, rows: Math.ceil(idx / cols) };
  }, [bucketCounts]);

  const total = dots.dots.length;
  const empty = total === 0;

  // SVG layout — fixed cell size keeps dot density constant.
  const CELL = 16;
  const DOT_R = 5;
  const PAD = 8;
  const width = dots.cols * CELL + PAD * 2;
  const height = Math.max(1, dots.rows) * CELL + PAD * 2;

  return (
    <section
      data-testid="voter-influence-graph"
      className="space-y-3 rounded-md border border-border bg-surface p-4"
    >
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold uppercase tracking-wider text-text-muted">
            100-voter influence graph
          </h3>
          <p className="text-xs text-text-muted">
            {voterCount} voters · colored by final bucket
          </p>
        </div>
        <Legend bucketCounts={bucketCounts} total={total} />
      </header>

      {empty ? (
        <p
          data-testid="voter-graph-empty"
          className="rounded-md border border-border bg-surface-elevated p-3 text-xs text-text-muted"
        >
          Voter distribution data is not available for this run.
        </p>
      ) : (
        <div className="flex justify-center">
          <svg
            data-testid="voter-graph-svg"
            role="img"
            aria-label={`100-voter influence graph: ${bucketCounts.buyer} buyer, ${bucketCounts.receptive} receptive, ${bucketCounts.uncertain} uncertain, ${bucketCounts.skeptical} skeptical`}
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            className="overflow-visible"
          >
            {dots.dots.map((d) => (
              <circle
                key={d.index}
                data-testid={`voter-dot-${d.bucket}`}
                cx={PAD + d.x * CELL + CELL / 2}
                cy={PAD + d.y * CELL + CELL / 2}
                r={DOT_R}
                fill={d.fill}
                opacity={0.92}
              />
            ))}
          </svg>
        </div>
      )}

      <p className="text-xs italic text-text-muted">
        Debate agents talk. {voterCount} voters absorb and spread.
      </p>

      <p
        data-testid="voter-graph-not-debate-agents-note"
        className="text-[11px] leading-relaxed text-text-muted"
      >
        These dots represent the 100-voter overlay, not LLM debate
        agents. Voters react to the deep-agent debate arguments and
        propagate them through a 4-round influence loop. No new LLM
        calls per voter.
      </p>
    </section>
  );
}

function Legend({
  bucketCounts,
  total,
}: {
  bucketCounts: Record<BucketKey, number>;
  total: number;
}) {
  return (
    <div
      data-testid="voter-graph-legend"
      className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-text-muted"
    >
      {BUCKET_ORDER.map((b) => {
        const n = bucketCounts[b];
        const pct = total > 0 ? Math.round((n / total) * 100) : 0;
        return (
          <span
            key={b}
            data-testid={`voter-graph-legend-${b}`}
            className="inline-flex items-center gap-1.5"
          >
            <span
              aria-hidden
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: BUCKET_DOT_FILL[b] }}
            />
            <span className="text-text-body">{BUCKET_LABEL[b]}</span>
            <span className="font-mono">{n}</span>
            <span>({pct}%)</span>
          </span>
        );
      })}
    </div>
  );
}
