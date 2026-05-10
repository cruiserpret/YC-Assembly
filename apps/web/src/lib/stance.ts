// Phase 10B+ — stance helpers used across transcript / graph / live
// distribution components. Maps the closed 5-stance discussion enum
// onto a coarser FOR / AGAINST / NEUTRAL bucket for high-density UI,
// and onto a numeric score so we can render shift magnitudes
// (▲ 0.X / ▼ 0.X) per turn.

// Internal bucket name (stable, used in code paths). The user-facing
// label is the founder-friendly term — "Receptive / Uncertain /
// Resistant" — replacing the older FOR / NEUTRAL / AGAINST framing
// because the latter read like a verdict or political vote.
export type StanceBucket = "for" | "against" | "neutral";

export interface BucketStyle {
  /** Human-readable founder-facing label. Title case. */
  label: string;
  /** All-caps short version used in tight pills. */
  shortLabel: string;
  textClass: string;
  borderClass: string;
  fillClass: string;
  dotClass: string;
}

const BUCKET_STYLES: Record<StanceBucket, BucketStyle> = {
  for: {
    label: "Receptive",
    shortLabel: "RECEPTIVE",
    textClass: "text-accent",
    borderClass: "border-accent-border",
    fillClass: "bg-accent",
    dotClass: "bg-accent",
  },
  against: {
    label: "Resistant",
    shortLabel: "RESISTANT",
    textClass: "text-danger",
    borderClass: "border-danger/40",
    fillClass: "bg-danger",
    dotClass: "bg-danger",
  },
  neutral: {
    label: "Uncertain",
    shortLabel: "UNCERTAIN",
    textClass: "text-text-muted",
    borderClass: "border-border",
    fillClass: "bg-text-muted",
    dotClass: "bg-text-muted",
  },
};

const STANCE_TO_BUCKET: Record<string, StanceBucket> = {
  // FOR — the persona is moving toward / signaling acceptance
  interested_if_proven: "for",
  would_buy_now: "for",
  would_try_once: "for",
  would_join_waitlist: "for",
  would_share_with_friend: "for",
  would_consider_if_proven: "for",
  // AGAINST — the persona is signaling rejection / loyalty elsewhere
  skeptical: "against",
  likely_reject: "against",
  would_reject: "against",
  would_block: "against",
  loyal_to_current_alternative: "against",
  // NEUTRAL — undecided / wants more info / actively comparing
  curious_but_unconvinced: "neutral",
  needs_more_information: "neutral",
  would_compare_to_current_brand: "neutral",
};

const STANCE_TO_SCORE: Record<string, number> = {
  // Negative pole = AGAINST, positive pole = FOR. Used to compute
  // round-on-round shift magnitudes per persona.
  likely_reject: -2,
  would_reject: -2,
  would_block: -2,
  loyal_to_current_alternative: -1.5,
  skeptical: -1,
  needs_more_information: 0,
  would_compare_to_current_brand: 0,
  curious_but_unconvinced: 0.5,
  would_consider_if_proven: 1,
  interested_if_proven: 1,
  would_join_waitlist: 1.5,
  would_try_once: 1.5,
  would_share_with_friend: 1.5,
  would_buy_now: 2,
};

const SCORE_RANGE = 4; // -2 .. +2 — used to normalize shift to [-1, 1]

export function bucketStance(stance: string | null | undefined): StanceBucket {
  if (!stance) return "neutral";
  return STANCE_TO_BUCKET[stance] ?? "neutral";
}

export function bucketStyle(bucket: StanceBucket): BucketStyle {
  return BUCKET_STYLES[bucket];
}

export function stanceScore(stance: string | null | undefined): number {
  if (!stance) return 0;
  return STANCE_TO_SCORE[stance] ?? 0;
}

/** Normalized shift in [-1, 1]. Positive = moved toward FOR;
 *  negative = moved toward AGAINST; magnitude = how big the move. */
export function stanceShift(
  fromStance: string | null | undefined,
  toStance: string | null | undefined,
): number {
  const a = stanceScore(fromStance);
  const b = stanceScore(toStance);
  return (b - a) / SCORE_RANGE;
}

/** Format a normalized shift as a UI string. Returns null when shift
 *  is too small to be worth showing (< 0.04 ≈ same bucket). */
export function formatShift(shift: number): {
  arrow: "▲" | "▼" | null;
  magnitude: string;
  toneClass: string;
} {
  const abs = Math.abs(shift);
  if (abs < 0.04) {
    return { arrow: null, magnitude: "", toneClass: "text-text-muted" };
  }
  return {
    arrow: shift > 0 ? "▲" : "▼",
    magnitude: abs.toFixed(2),
    toneClass: shift > 0 ? "text-accent" : "text-danger",
  };
}
