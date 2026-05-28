// Phase 10B+ — bucket → natural-language sentence mappers.
// Used by ReportDashboard to render objections / proof needs as
// founder-friendly sentences instead of raw bucket slugs + scores.
//
// Unknown buckets fall back to a humanized version of the slug.

const OBJECTION_SENTENCES: Record<string, string> = {
  trust_or_review_gap:
    "Personas wanted independent reviews and proof from real customers before they'd consider switching.",
  price_value_concern:
    "Several personas weren't convinced the price was justified for what was on offer.",
  price_or_value_signal:
    "Personas weighed price against perceived value and pushed back when the math didn't land.",
  specs_not_disclosed:
    "Personas pushed back on missing or vague technical specifications.",
  competitor_already_solves:
    "A subset already use a competitor that solves the same problem; switching needs a clear reason.",
  battery_or_runtime_concern:
    "Battery life and run-time were called out as critical to confidence.",
  no_ip_rating_or_durability_proof:
    "Personas wanted explicit durability or weather-resistance proof.",
  no_use_case_fit:
    "A few personas didn't see how the product fit their daily routine.",
  brand_credibility:
    "Brand credibility came up — personas wanted some signal the maker is here to stay.",
  customer_service_concern:
    "Personas worried about support and what happens when something breaks.",
  fitment_or_compatibility:
    "Compatibility with what personas already own was a recurring concern.",
  shipping_or_availability:
    "Shipping availability or delivery timing was raised as a friction.",
};

const PROOF_SENTENCES: Record<string, string> = {
  head_to_head_comparison:
    "A side-by-side comparison against named alternatives would be the most convincing proof.",
  third_party_review:
    "Independent third-party reviews carry the most weight for these personas.",
  warranty_or_returns:
    "A clear return policy or warranty would lower the perceived risk.",
  battery_runtime_proof:
    "Demonstrated battery / run-time numbers would directly answer the top concern.",
  durability_test:
    "A documented durability test or pilot would close a key objection.",
  lumens_disclosure:
    "Disclosing measured output (e.g. lumens) would address performance concerns.",
  trust_proof_signal:
    "Verified proof — measured numbers, not marketing — would shift these personas.",
  field_pilot:
    "A real-world field pilot with documented outcomes would carry the most weight.",
  spec_sheet:
    "A full spec sheet, openly published, would resolve the technical objections.",
  customer_testimonial:
    "Real-customer testimonials (with names and context) would help.",
  press_or_industry_coverage:
    "Coverage in trusted industry outlets would lend credibility.",
};

/** Convert a snake_case bucket slug into a human-readable phrase
 *  used as a fallback when no specific sentence is mapped. */
export function humanizeBucket(slug: string): string {
  return slug
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((p, i) =>
      i === 0
        ? p.charAt(0).toUpperCase() + p.slice(1)
        : p
    )
    .join(" ");
}

export function objectionSentence(bucket: string): string {
  if (OBJECTION_SENTENCES[bucket]) return OBJECTION_SENTENCES[bucket];
  return `Personas raised concerns around ${humanizeBucket(bucket).toLowerCase()}.`;
}

export function proofSentence(bucket: string): string {
  if (PROOF_SENTENCES[bucket]) return PROOF_SENTENCES[bucket];
  return `Personas asked for proof in the form of ${humanizeBucket(bucket).toLowerCase()}.`;
}

// ---------------------------------------------------------------------
// Phase 14B — physical-product objection / proof buckets.
//
// The LLM persona generator picks bucket slugs from a pre-defined
// vocabulary that was originally built around physical products
// (durability, weather resistance, IP rating, battery life, shipping,
// fitment). On software / digital products this vocabulary
// occasionally leaks low-score "noise" buckets — e.g. a 0.06-weighted
// `no_ip_rating_or_durability_proof` objection on an AI knowledge-base
// product. Those low-score physical-product buckets are LLM artifacts,
// not real signal from the debate.
//
// `filterApplicableObjectionBuckets` drops physical-product buckets
// from the rendered list when:
//   1. The product is detected as software / digital, AND
//   2. The bucket's weighted_score is below the strong-signal floor.
//
// Physical products (or non-software briefs that mention these
// concerns at high weight) keep these buckets — the filter is
// category-aware, not blanket suppression.
// ---------------------------------------------------------------------

const PHYSICAL_OBJECTION_BUCKETS = new Set<string>([
  "no_ip_rating_or_durability_proof",
  "battery_or_runtime_concern",
  "shipping_or_availability",
  "fitment_or_compatibility",
]);

const PHYSICAL_PROOF_BUCKETS = new Set<string>([
  "battery_runtime_proof",
  "durability_test",
  "lumens_disclosure",
]);

const STRONG_SIGNAL_FLOOR = 0.15;

const SOFTWARE_PRODUCT_HINTS = [
  "ai", "software", "saas", "platform", "app", "application",
  "tool", "service", "api", "knowledge base", "knowledgebase",
  "agent", "assistant", "chatbot", "automation", "workflow",
  "dashboard", "analytics", "browser", "extension", "plugin",
  "cli", "library", "framework", "model", "llm", "ml",
];

const PHYSICAL_PRODUCT_HINTS = [
  "device", "hardware", "wearable", "bottle", "lamp", "light",
  "bag", "shoes", "watch", "headphones", "speaker", "appliance",
  "snack", "drink", "food", "beverage", "kit", "kits",
  "battery", "charger", "case", "sleeve", "strap", "garment",
];

export function isLikelySoftwareProduct(
  productBrief: Record<string, unknown> | null | undefined,
): boolean {
  if (!productBrief) return false;
  const blob = [
    productBrief.product_type,
    productBrief.category_hint,
    productBrief.product_name,
    productBrief.product_description,
  ]
    .filter((x): x is string => typeof x === "string")
    .join(" ")
    .toLowerCase();
  if (!blob) return false;
  const physicalHit = PHYSICAL_PRODUCT_HINTS.some((h) =>
    blob.includes(h),
  );
  if (physicalHit) return false;
  return SOFTWARE_PRODUCT_HINTS.some((h) => blob.includes(h));
}

/** Drop physical-product objection buckets from the rendered list
 *  when the brief is non-physical AND the bucket's weighted_score
 *  is below the strong-signal floor (0.15). Physical-product briefs
 *  AND high-weight physical buckets on any brief are preserved. */
export function filterApplicableObjectionBuckets<
  T extends { bucket: string; weighted_score?: number },
>(items: T[], productBrief: Record<string, unknown> | null | undefined): T[] {
  if (!isLikelySoftwareProduct(productBrief)) return items;
  return items.filter((item) => {
    if (!PHYSICAL_OBJECTION_BUCKETS.has(item.bucket)) return true;
    return (item.weighted_score ?? 0) >= STRONG_SIGNAL_FLOOR;
  });
}

/** Same as filterApplicableObjectionBuckets but for proof buckets. */
export function filterApplicableProofBuckets<
  T extends { bucket: string; weighted_score?: number },
>(items: T[], productBrief: Record<string, unknown> | null | undefined): T[] {
  if (!isLikelySoftwareProduct(productBrief)) return items;
  return items.filter((item) => {
    if (!PHYSICAL_PROOF_BUCKETS.has(item.bucket)) return true;
    return (item.weighted_score ?? 0) >= STRONG_SIGNAL_FLOOR;
  });
}
