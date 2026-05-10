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
