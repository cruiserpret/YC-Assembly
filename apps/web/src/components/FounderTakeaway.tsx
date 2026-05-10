// Phase 10B+ refinement — Founder takeaway box.
//
// A short, plain-English summary of what the synthetic society
// implies for the founder's product, derived purely from data we
// already have on the page (transcript bucket distribution + top
// objection bucket + best-fit role names). Nothing is invented;
// nothing is forecast; no launch/kill verdict.

import { useMemo } from "react";
import { objectionSentence } from "@/lib/buckets";
import { humanizeRole } from "@/lib/labels";
import { bucketStance } from "@/lib/stance";
import type {
  DiscussionTranscriptPayload,
  FounderReport,
} from "@/lib/types";

export interface FounderTakeawayProps {
  report: FounderReport;
  transcript: DiscussionTranscriptPayload;
}

export function FounderTakeaway({
  report,
  transcript,
}: FounderTakeawayProps) {
  const summary = useMemo(
    () => synthesizeTakeaway(report, transcript),
    [report, transcript],
  );

  return (
    <section
      data-testid="founder-takeaway"
      className="rounded-md border border-accent-border bg-accent-soft p-6"
    >
      <header className="mb-3 flex items-center gap-2">
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full bg-accent"
        />
        <h3 className="font-mono text-xs uppercase tracking-[0.2em] text-accent">
          Founder takeaway
        </h3>
      </header>
      <p className="text-base leading-relaxed text-text-primary">
        {summary}
      </p>
    </section>
  );
}

function synthesizeTakeaway(
  report: FounderReport,
  transcript: DiscussionTranscriptPayload,
): string {
  // 1. Stance distribution — is this society mostly receptive,
  //    contested, undecided, or skeptical?
  let f = 0;
  let a = 0;
  let n = 0;
  const finalStanceByPid: Record<string, string | null> = {};
  for (const [pid, b] of Object.entries(transcript.private_ballots)) {
    const stance =
      b.final?.stance ?? b.reflection?.stance ?? b.pre?.stance ?? null;
    finalStanceByPid[pid] = stance;
    const bucket = bucketStance(stance);
    if (bucket === "for") f += 1;
    else if (bucket === "against") a += 1;
    else n += 1;
  }
  const total = f + a + n || 1;
  const forPct = f / total;
  const againstPct = a / total;
  const neutralPct = n / total;
  let leanPhrase: string;
  if (forPct >= 0.6) {
    leanPhrase = "mostly receptive, but only after proof";
  } else if (forPct >= 0.45 && forPct > againstPct) {
    leanPhrase = "leaning receptive, with proof as the gating condition";
  } else if (againstPct >= 0.4) {
    leanPhrase = "contested — a sizeable group resists the value proposition";
  } else if (neutralPct >= 0.5) {
    leanPhrase = "still uncertain — interest exists, but the room is undecided";
  } else {
    leanPhrase = "split — there&apos;s no clear majority view";
  }

  // 2. Top objection bucket → translate to a phrase fragment
  const topObjection = (report.top_objections || [])
    .slice()
    .sort(
      (x, y) => (y.weighted_score ?? 0) - (x.weighted_score ?? 0),
    )[0];
  const blockerSentence = topObjection
    ? `The biggest blocker was ${objectionBucketAsBlocker(topObjection.bucket)}, not the core product idea.`
    : "";

  // 3. Best-fit roles by receptive count
  const roleCounts = new Map<
    string,
    { display: string; total: number; receptive: number }
  >();
  for (const g of transcript.groups) {
    for (const p of g.personas) {
      let entry = roleCounts.get(p.role);
      if (!entry) {
        entry = { display: humanizeRole(p.role), total: 0, receptive: 0 };
        roleCounts.set(p.role, entry);
      }
      entry.total += 1;
      if (bucketStance(finalStanceByPid[p.persona_id]) === "for") {
        entry.receptive += 1;
      }
    }
  }
  const bestFitRoles = [...roleCounts.values()]
    .filter((r) => r.receptive >= 2)
    .sort((x, y) => y.receptive - x.receptive)
    .slice(0, 2)
    .map((r) => r.display);

  let audienceSentence = "";
  if (bestFitRoles.length === 1) {
    audienceSentence = `The strongest early audience appears to be ${bestFitRoles[0].toLowerCase()}.`;
  } else if (bestFitRoles.length >= 2) {
    audienceSentence = `The strongest early audience appears to be ${bestFitRoles[0].toLowerCase()} and ${bestFitRoles[1].toLowerCase()}.`;
  }

  return [
    `This synthetic society was ${leanPhrase}.`,
    blockerSentence,
    audienceSentence,
  ]
    .filter(Boolean)
    .join(" ");
}

/**
 * Take an objection bucket slug and turn it into a noun-phrase
 * fragment that fits inside "the biggest blocker was ___".
 * Falls back to the generic objection sentence shape if the slug
 * isn't in our specialized vocabulary.
 */
function objectionBucketAsBlocker(slug: string): string {
  const map: Record<string, string> = {
    trust_or_review_gap: "trust and reviews",
    price_value_concern: "price-vs-value perception",
    price_or_value_signal: "price-vs-value perception",
    specs_not_disclosed: "missing or vague technical specs",
    competitor_already_solves: "an incumbent that already solves the problem",
    battery_or_runtime_concern: "battery / run-time confidence",
    no_ip_rating_or_durability_proof: "missing durability proof",
    no_use_case_fit: "lack of a clear daily use case",
    brand_credibility: "brand credibility",
    customer_service_concern: "support and warranty doubts",
    fitment_or_compatibility: "compatibility with what people already own",
    shipping_or_availability: "shipping and availability friction",
  };
  if (map[slug]) return map[slug];
  // Fallback — strip ".." prefix from objectionSentence to land
  // mid-sentence
  const s = objectionSentence(slug);
  return s.replace(/^Personas (raised |wanted |pushed back on |…)?/i, "")
    .replace(/^./, (c) => c.toLowerCase())
    .replace(/\.$/, "");
}
