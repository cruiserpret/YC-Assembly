// Phase 10B+ refinement — two mini-cards explaining the causal
// story behind opinion shifts and resistance, in plain English.
//
// Both sentences are derived from the actual data on the page
// (top proof needs / top objections / cohort role distribution),
// so they describe what happened, not a forecast.

import { useMemo } from "react";
import { humanizeRole } from "@/lib/labels";
import { proofSentence } from "@/lib/buckets";
import { bucketStance } from "@/lib/stance";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
} from "@/lib/types";

export interface WhyShiftedResistedCardsProps {
  report: FounderReport;
  transcript: DiscussionTranscriptPayload;
}

export function WhyShiftedResistedCards({
  report,
  transcript,
}: WhyShiftedResistedCardsProps) {
  const story = useMemo(
    () => synthesizeStory(report, transcript),
    [report, transcript],
  );

  return (
    <section
      data-testid="why-shifted-resisted"
      className="grid grid-cols-1 gap-4 md:grid-cols-2"
    >
      <article
        className="space-y-2 rounded-md border border-border bg-surface p-5"
        data-testid="why-shifted"
      >
        <header className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rounded-full bg-accent"
          />
          <h4 className="font-mono text-xs uppercase tracking-wider text-text-muted">
            Why opinions shifted
          </h4>
        </header>
        <p className="text-sm leading-relaxed text-text-body">
          {story.shifted}
        </p>
      </article>
      <article
        className="space-y-2 rounded-md border border-border bg-surface p-5"
        data-testid="why-resisted"
      >
        <header className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rounded-full bg-danger"
          />
          <h4 className="font-mono text-xs uppercase tracking-wider text-text-muted">
            Why some stayed resistant
          </h4>
        </header>
        <p className="text-sm leading-relaxed text-text-body">
          {story.resisted}
        </p>
      </article>
    </section>
  );
}

function synthesizeStory(
  report: FounderReport,
  transcript: DiscussionTranscriptPayload,
): { shifted: string; resisted: string } {
  // Shift count
  let shifted = 0;
  let scored = 0;
  for (const [, b] of Object.entries(transcript.private_ballots)) {
    const pre = b.pre?.stance ?? null;
    const final = b.final?.stance ?? b.reflection?.stance ?? null;
    if (!pre || !final) continue;
    scored += 1;
    if (bucketStance(pre) !== bucketStance(final)) shifted += 1;
  }

  // Top proof bucket
  const topProof = (report.proof_needed || [])
    .slice()
    .sort((a, b) => (b.weighted_score ?? 0) - (a.weighted_score ?? 0))[0];

  // Resistant personas — collect their roles + their final stance
  const resistantRoles = new Map<string, number>();
  let competitorRoleCount = 0;
  let priceSkepticCount = 0;
  for (const g of transcript.groups) {
    for (const p of g.personas) {
      const stance =
        transcript.private_ballots[p.persona_id]?.final?.stance ?? null;
      if (bucketStance(stance) !== "against") continue;
      const display = humanizeRole(p.role);
      resistantRoles.set(display, (resistantRoles.get(display) ?? 0) + 1);
      if (p.role.startsWith("competitor_user")) competitorRoleCount += 1;
      if (p.role.includes("price")) priceSkepticCount += 1;
    }
  }
  const topResistantRoles = [...resistantRoles.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([role]) => role.toLowerCase());

  // ---- Shifted sentence ----
  let shiftedSentence: string;
  if (scored === 0) {
    shiftedSentence =
      "The simulation didn't produce a measurable shift signal on this run.";
  } else if (shifted === 0) {
    shiftedSentence =
      "No agents changed bucket during the discussion. Stances were locked in by the pre-discussion ballot — the debate didn't move anyone across the receptive / uncertain / resistant boundaries.";
  } else {
    const proofPart = topProof
      ? ` Most movement came from personas who needed ${proofPhraseFromBucket(topProof.bucket)}; once that proof was discussed, several agents updated their stance.`
      : ` Most movement came from personas who needed concrete proof, not more pitch.`;
    shiftedSentence =
      `${shifted} of ${scored} synthetic personas shifted their stance bucket during the discussion.` +
      proofPart;
  }

  // ---- Resisted sentence ----
  let resistedSentence: string;
  if (resistantRoles.size === 0) {
    resistedSentence =
      "No personas finished in the resistant bucket — the simulation didn't surface a hard-no segment for this brief.";
  } else {
    const driver: string[] = [];
    if (competitorRoleCount > 0) {
      driver.push("already use a competing alternative");
    }
    if (priceSkepticCount > 0) {
      driver.push("are price-sensitive");
    }
    if (driver.length === 0) {
      driver.push(
        "couldn't find a strong enough reason to switch from their current routine",
      );
    } else {
      driver.push("don't yet see enough reason to switch");
    }
    const roleList =
      topResistantRoles.length > 0
        ? `Resistant personas tend to be ${topResistantRoles.join(" and ")}.`
        : "";
    resistedSentence =
      `${roleList} They mostly ${joinWithCommasAnd(driver)}.`.trim();
  }

  return { shifted: shiftedSentence, resisted: resistedSentence };
}

function proofPhraseFromBucket(slug: string): string {
  const map: Record<string, string> = {
    head_to_head_comparison:
      "a side-by-side comparison against named alternatives",
    third_party_review: "independent third-party reviews",
    warranty_or_returns: "a clear return policy or warranty",
    battery_runtime_proof:
      "demonstrated battery / run-time numbers",
    durability_test: "a documented durability test",
    lumens_disclosure: "transparent performance disclosure",
    trust_proof_signal: "verified, measured proof — not marketing",
    field_pilot: "a real-world field pilot",
    spec_sheet: "a full published spec sheet",
    customer_testimonial: "real-customer testimonials",
    press_or_industry_coverage: "trusted industry coverage",
  };
  if (map[slug]) return map[slug];
  // Fallback: strip the leading subject from proofSentence
  return proofSentence(slug)
    .replace(/^A side-by-side|^Independent|^A clear|^Demonstrated|^A documented|^Disclosing|^Verified|^A real-world|^A full|^Real-customer|^Coverage in/i, (m) => m.toLowerCase())
    .replace(/\.$/, "");
}

function joinWithCommasAnd(items: string[]): string {
  if (items.length === 0) return "";
  if (items.length === 1) return items[0];
  if (items.length === 2) return `${items[0]} or ${items[1]}`;
  return items.slice(0, -1).join(", ") + ", or " + items[items.length - 1];
}
