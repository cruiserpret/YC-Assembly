"use client";
// Phase 10B+ — live distribution sidebar.
// Shows running FOR / AGAINST / NEUTRAL counts per round, computed
// from the transcript turns. Reflects the same bucket logic the
// AgentGraph uses so both views stay in sync.

import { useMemo } from "react";
import { bucketStance } from "@/lib/stance";
import type { DiscussionTranscriptPayload } from "@/lib/types";

export interface LiveDistributionProps {
  transcript: DiscussionTranscriptPayload;
  selectedRound?: number;
}

interface RoundCounts {
  round_number: number;
  round_label: string;
  for: number;
  against: number;
  neutral: number;
  total: number;
}

export function LiveDistribution({
  transcript,
  selectedRound,
}: LiveDistributionProps) {
  const roundCounts = useMemo(
    () => computeRoundCounts(transcript),
    [transcript],
  );
  const finalStanceCounts = useMemo(() => {
    let f = 0;
    let a = 0;
    let n = 0;
    for (const [, b] of Object.entries(transcript.private_ballots)) {
      const stance = b.final?.stance ?? null;
      const bucket = bucketStance(stance);
      if (bucket === "for") f += 1;
      else if (bucket === "against") a += 1;
      else n += 1;
    }
    return { for: f, against: a, neutral: n, total: f + a + n };
  }, [transcript]);

  const round =
    selectedRound !== undefined
      ? roundCounts.find((r) => r.round_number === selectedRound)
      : roundCounts[roundCounts.length - 1];

  return (
    <section
      data-testid="live-distribution"
      className="space-y-4 rounded-md border border-border bg-surface p-4 text-sm"
    >
      <header>
        <h3 className="text-xs uppercase tracking-wider text-text-muted">
          Live distribution
          {round ? (
            <span className="ml-2 text-text-body">
              · Round {round.round_number}
            </span>
          ) : null}
        </h3>
      </header>

      {round ? (
        <DistroBlock
          for_={round.for}
          against={round.against}
          neutral={round.neutral}
          total={round.total}
        />
      ) : (
        <p className="text-xs text-text-muted">No round selected.</p>
      )}

      <div className="border-t border-border pt-3">
        <p className="mb-2 text-xs uppercase tracking-wider text-text-muted">
          Final ballot
        </p>
        <DistroBlock
          for_={finalStanceCounts.for}
          against={finalStanceCounts.against}
          neutral={finalStanceCounts.neutral}
          total={finalStanceCounts.total}
        />
      </div>

      <details className="border-t border-border pt-3">
        <summary className="cursor-pointer text-xs uppercase tracking-wider text-text-muted">
          All rounds
        </summary>
        <ul className="mt-2 space-y-2">
          {roundCounts.map((r) => (
            <li key={r.round_number} className="text-xs">
              <p className="mb-1 text-text-body">
                Round {r.round_number}{" "}
                <span className="text-text-muted">· {r.round_label}</span>
              </p>
              <DistroBlock
                for_={r.for}
                against={r.against}
                neutral={r.neutral}
                total={r.total}
                compact
              />
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function DistroBlock({
  for_,
  against,
  neutral,
  total,
  compact,
}: {
  for_: number;
  against: number;
  neutral: number;
  total: number;
  compact?: boolean;
}) {
  if (total === 0) {
    return (
      <p className="text-xs text-text-muted">No turns recorded yet.</p>
    );
  }
  return (
    <ul
      className={`space-y-1 ${compact ? "text-[11px]" : "text-xs"}`}
      data-testid="distribution-rows"
    >
      <DistroRow
        label="Receptive"
        count={for_}
        total={total}
        toneClass="bg-accent"
        textClass="text-accent"
      />
      <DistroRow
        label="Uncertain"
        count={neutral}
        total={total}
        toneClass="bg-text-muted"
        textClass="text-text-muted"
      />
      <DistroRow
        label="Resistant"
        count={against}
        total={total}
        toneClass="bg-danger"
        textClass="text-danger"
      />
    </ul>
  );
}

function DistroRow({
  label,
  count,
  total,
  toneClass,
  textClass,
}: {
  label: string;
  count: number;
  total: number;
  toneClass: string;
  textClass: string;
}) {
  const pct = total > 0 ? (count / total) * 100 : 0;
  return (
    <li className="flex items-center gap-2">
      <span
        className={`min-w-[5rem] text-xs font-medium ${textClass}`}
      >
        {label}
      </span>
      <span
        className="h-1.5 flex-1 overflow-hidden rounded-sm bg-border"
        aria-hidden
      >
        <span
          className={`block h-full ${toneClass}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="w-6 text-right font-mono text-text-body">{count}</span>
    </li>
  );
}

function computeRoundCounts(
  transcript: DiscussionTranscriptPayload,
): RoundCounts[] {
  const map = new Map<number, RoundCounts>();
  for (const g of transcript.groups) {
    for (const r of g.rounds) {
      let entry = map.get(r.round_number);
      if (!entry) {
        entry = {
          round_number: r.round_number,
          round_label: r.round_label,
          for: 0,
          against: 0,
          neutral: 0,
          total: 0,
        };
        map.set(r.round_number, entry);
      }
      for (const t of r.turns) {
        const bucket = bucketStance(t.stance);
        if (bucket === "for") entry.for += 1;
        else if (bucket === "against") entry.against += 1;
        else entry.neutral += 1;
        entry.total += 1;
      }
    }
  }
  return [...map.values()].sort((a, b) => a.round_number - b.round_number);
}
