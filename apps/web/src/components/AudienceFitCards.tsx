// Phase 10B+ refinement — Best-fit and Hardest-to-convince
// audience cards. Both rows are derived from the role distribution
// in the transcript: best-fit = roles with the highest receptive
// count; hardest-to-convince = roles with the highest resistant
// count, plus any competitor-user / price-skeptic roles that show
// up regardless of bucket.

import { useMemo } from "react";
import { humanizeRole } from "@/lib/labels";
import { bucketStance } from "@/lib/stance";
import type { DiscussionTranscriptPayload } from "@/lib/types";

export interface AudienceFitCardsProps {
  transcript: DiscussionTranscriptPayload;
}

interface RoleRow {
  rawRole: string;
  display: string;
  receptive: number;
  resistant: number;
  uncertain: number;
  total: number;
}

type HardestKind = "resistant" | "uncertain" | "all_receptive";

export function AudienceFitCards({ transcript }: AudienceFitCardsProps) {
  const { bestFit, hardest, hardestKind, summary } = useMemo(
    () => deriveAudienceFit(transcript),
    [transcript],
  );

  return (
    <section
      data-testid="audience-fit-cards"
      className="grid grid-cols-1 gap-4 md:grid-cols-2"
    >
      <article
        className="space-y-3 rounded-md border border-accent-border/50 bg-surface p-5"
        data-testid="best-fit-card"
      >
        <header className="flex items-center justify-between">
          <h4 className="font-mono text-xs uppercase tracking-wider text-accent">
            Best-fit audience
          </h4>
        </header>
        {bestFit.length === 0 ? (
          <p className="text-sm text-text-muted">
            No roles finished receptive on this run.
          </p>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-text-body">
              {summary.best}
            </p>
            {/* Phase 10B.5 — role labels are SUPPORTING detail, not
                the primary copy. The natural-language summary above
                describes the audience; the list below shows the
                simulation roles those personas were anchored to. */}
            <p className="text-[11px] uppercase tracking-wider text-text-muted">
              Simulation roles in this audience
            </p>
            <ul className="space-y-1.5 text-sm">
              {bestFit.map((r) => (
                <li
                  key={r.rawRole}
                  className="flex items-center justify-between rounded-md border border-border bg-surface-elevated px-3 py-2"
                >
                  <span className="text-text-muted">{r.display}</span>
                  <span className="font-mono text-accent">
                    {r.receptive}
                    <span className="ml-1 text-text-muted">
                      / {r.total}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </article>

      <article
        className="space-y-3 rounded-md border border-danger/40 bg-surface p-5"
        data-testid="hardest-card"
      >
        <header className="flex items-center justify-between">
          <h4 className="font-mono text-xs uppercase tracking-wider text-danger">
            Hardest-to-convince audience
          </h4>
        </header>
        {hardest.length === 0 ? (
          <p className="text-sm text-text-muted">
            No friction pattern surfaced on this run.
          </p>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-text-body">
              {summary.hardest}
            </p>
            <p className="text-[11px] uppercase tracking-wider text-text-muted">
              Simulation roles in this audience
            </p>
            <ul className="space-y-1.5 text-sm">
              {hardest.map((r) => {
                // Phase 10B.3: when there are no resistant rows
                // we display the uncertain count under the danger
                // accent so the card still tells a story.
                const value =
                  hardestKind === "resistant"
                    ? r.resistant
                    : hardestKind === "uncertain"
                      ? r.uncertain
                      : r.total - r.receptive;
                return (
                  <li
                    key={r.rawRole}
                    className="flex items-center justify-between rounded-md border border-border bg-surface-elevated px-3 py-2"
                  >
                    <span className="text-text-muted">{r.display}</span>
                    <span className="font-mono text-danger">
                      {value}
                      <span className="ml-1 text-text-muted">
                        / {r.total}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </article>
    </section>
  );
}

function deriveAudienceFit(transcript: DiscussionTranscriptPayload): {
  bestFit: RoleRow[];
  hardest: RoleRow[];
  hardestKind: HardestKind;
  summary: { best: string; hardest: string };
} {
  const stanceByPid: Record<string, string | null> = {};
  for (const [pid, b] of Object.entries(transcript.private_ballots)) {
    stanceByPid[pid] =
      b.final?.stance ?? b.reflection?.stance ?? b.pre?.stance ?? null;
  }
  const seen = new Set<string>();
  const byRole = new Map<string, RoleRow>();
  for (const g of transcript.groups) {
    for (const p of g.personas) {
      if (seen.has(p.persona_id)) continue;
      seen.add(p.persona_id);
      let entry = byRole.get(p.role);
      if (!entry) {
        entry = {
          rawRole: p.role,
          display: humanizeRole(p.role),
          receptive: 0,
          resistant: 0,
          uncertain: 0,
          total: 0,
        };
        byRole.set(p.role, entry);
      }
      entry.total += 1;
      const bucket = bucketStance(stanceByPid[p.persona_id]);
      if (bucket === "for") entry.receptive += 1;
      else if (bucket === "against") entry.resistant += 1;
      else entry.uncertain += 1;
    }
  }

  const bestFit = [...byRole.values()]
    .filter((r) => r.receptive > 0)
    .sort((a, b) => b.receptive - a.receptive)
    .slice(0, 4);

  // Phase 10B.3: hardest-to-convince must populate even when there
  // are zero resistant ballots — fall back to the highest-uncertain
  // rows. The report layer also computes a structured copy block;
  // the frontend can use that when present (future wiring).
  let hardest = [...byRole.values()]
    .filter((r) => r.resistant > 0)
    .sort((a, b) => b.resistant - a.resistant)
    .slice(0, 4);
  let hardestKind: "resistant" | "uncertain" | "all_receptive" =
    "resistant";
  if (hardest.length === 0) {
    hardest = [...byRole.values()]
      .filter((r) => r.uncertain > 0)
      .sort((a, b) => b.uncertain - a.uncertain)
      .slice(0, 4);
    hardestKind = "uncertain";
  }
  if (hardest.length === 0) {
    // All cohorts finished receptive. Pick the rows with the
    // smallest receptive count as the still-friction-iest.
    hardest = [...byRole.values()]
      .sort((a, b) => a.receptive - b.receptive)
      .slice(0, 2);
    hardestKind = "all_receptive";
  }

  // Synthesize the natural-language summary at the top of each card
  let bestSentence = "";
  if (bestFit.length === 1) {
    bestSentence = `${bestFit[0].display} were the most receptive on this run. They understood the value quickly and mainly wanted proof that the product works as promised.`;
  } else if (bestFit.length >= 2) {
    bestSentence = `${bestFit[0].display} and ${bestFit[1].display.toLowerCase()} were the most receptive. They understood the problem quickly and mainly wanted proof that the product works as promised.`;
  }

  let hardestSentence = "";
  if (hardest.length > 0) {
    const competitorTopRole = hardest.find((r) =>
      r.rawRole.startsWith("competitor_user"),
    );
    const priceSkeptic = hardest.find((r) =>
      r.rawRole.includes("price"),
    );
    const trustSeeker = hardest.find((r) =>
      r.rawRole.startsWith("trust_seeker"),
    );
    const drivers: string[] = [];
    if (competitorTopRole) {
      drivers.push("already commit to a competing alternative");
    }
    if (priceSkeptic) {
      drivers.push("hold back on price-vs-value grounds");
    }
    if (trustSeeker) {
      drivers.push(
        "still need certification, third-party reviews, or material proof",
      );
    }
    if (drivers.length === 0) {
      drivers.push(
        "didn't see a strong enough reason to switch from their current routine",
      );
    } else {
      drivers.push("need a clearer reason to switch");
    }
    if (hardestKind === "resistant") {
      if (hardest.length === 1) {
        hardestSentence = `${hardest[0].display} were the hardest to move on this run. They mostly ${joinWithCommasAnd(drivers)}.`;
      } else {
        hardestSentence = `${hardest[0].display} and ${hardest[1].display.toLowerCase()} were the hardest to move. They mostly ${joinWithCommasAnd(drivers)}.`;
      }
    } else if (hardestKind === "uncertain") {
      if (hardest.length === 1) {
        hardestSentence = `No cohort fully rejected the concept, but ${hardest[0].display.toLowerCase()} still required stronger proof: they mostly ${joinWithCommasAnd(drivers)}.`;
      } else {
        hardestSentence = `No cohort fully rejected the concept, but ${hardest[0].display.toLowerCase()} and ${hardest[1].display.toLowerCase()} still required stronger proof: they mostly ${joinWithCommasAnd(drivers)}.`;
      }
    } else {
      hardestSentence = `Every cohort finished receptive on this run; ${hardest[0]?.display.toLowerCase() ?? "the audience"} flagged the most friction to clear before purchase.`;
    }
  }

  return {
    bestFit,
    hardest,
    hardestKind,
    summary: { best: bestSentence, hardest: hardestSentence },
  };
}

function joinWithCommasAnd(items: string[]): string {
  if (items.length === 0) return "";
  if (items.length === 1) return items[0];
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return items.slice(0, -1).join(", ") + ", and " + items[items.length - 1];
}
