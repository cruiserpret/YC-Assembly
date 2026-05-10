// Phase 10B+ — humanizers for snake_case enum values surfaced in the
// UI. The simulation talks in slugs; the founder doesn't have to.

const STANCE_LABELS: Record<string, string> = {
  // Discussion stance enum
  curious_but_unconvinced: "Curious but unconvinced",
  interested_if_proven: "Interested if proven",
  skeptical: "Skeptical",
  likely_reject: "Likely to reject",
  needs_more_information: "Needs more information",
  // Intent labels (also surfaced in some places)
  would_buy_now: "Would buy now",
  would_try_once: "Would try once",
  would_join_waitlist: "Would join waitlist",
  would_consider_if_proven: "Would consider if proven",
  would_share_with_friend: "Would share with a friend",
  would_compare_to_current_brand: "Would compare to current brand",
  loyal_to_current_alternative: "Loyal to current alternative",
  would_reject: "Would reject",
  would_block: "Would actively block",
  // Switching-status labels (surfaced in IntentSnapshot)
  no_current_alternative: "No current alternative",
  actively_comparing: "Actively comparing options",
  weakly_attached_to_alternative: "Weakly attached to current alternative",
  refuses_switching: "Refuses to switch",
};

export function humanizeStance(slug: string | null | undefined): string {
  if (!slug) return "—";
  if (STANCE_LABELS[slug]) return STANCE_LABELS[slug];
  return humanizeSlug(slug);
}

/**
 * Phase 10B.5 — universal label humanizer. Strips snake_case from
 * any switching-status / intent / stance slug surfaced in the UI.
 * Use this in any place that renders raw enum slugs to public users.
 */
export function humanizeLabel(slug: string | null | undefined): string {
  if (!slug) return "—";
  if (STANCE_LABELS[slug]) return STANCE_LABELS[slug];
  return humanizeSlug(slug);
}

export function humanizeRole(slug: string | null | undefined): string {
  if (!slug) return "Unknown";
  // "competitor_user_hidrate_spark" → "Hidrate Spark user"
  if (slug.startsWith("competitor_user_")) {
    const tail = slug.slice("competitor_user_".length);
    return `${humanizeSlug(tail)} user`;
  }
  return humanizeSlug(slug);
}

export function humanizeSlug(slug: string): string {
  return slug
    .split(/[_\s]+/)
    .filter(Boolean)
    .map(
      (part, i) =>
        (i === 0 ? part.charAt(0).toUpperCase() + part.slice(1) : part),
    )
    .join(" ");
}
