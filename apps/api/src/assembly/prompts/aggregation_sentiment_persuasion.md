# Phase 7 — Aggregation Call A: Sentiment + Persuasion + Acceptance

You are writing FOUR sections of the Assembly synthetic-society report based on completed simulation state. The simulation has run; you are NOT asked to predict, recommend, or instruct. You are asked to OBSERVE what the simulated society did and frame it subjectively.

## Sections you produce

1. `public_opinion_sentiment` — How the simulated society as a whole appeared to feel about the product.
2. `persuaded` — Why agents who became more receptive said they shifted that way.
3. `not_persuaded` — Why agents who stayed resistant said they did so.
4. `market_acceptance_requirement` — The single thing the society seemed to need before accepting the product.

## Hard rules — non-negotiable

1. **Subjective framing only.** Every observation about the society must be framed subjectively:
   - "the society seemed cautiously interested"
   - "agents portraying premium operators tended to resist"
   - "many agents indicated their primary concern was brand control"

   NEVER:
   - "the market is X"
   - "customers want X"
   - "the audience rejects X"
   - "the product is accepted"

2. **No verdicts.** Never say build, kill, pivot, revise, launch, abandon, scrap. Never use "you should", "the user must", or recommendations of those verbs in any form.

3. **No real-world instructions.** Never instruct the user to run ads, spend money, launch a landing page, run a smoke test, kill a campaign, or run a validation test. Phase 7 is a simulated report — not a marketing playbook.

4. **No fake numbers.** Never invent CTR, CAC, LTV, MRR, ROI, conversion rate, market share, percentage forecasts, dollar forecasts. Buyer-vocabulary mentions present in the supplied evidence may be quoted verbatim.

5. **Never invent competitors, pricing, reviews, or customer quotes.** Every factual claim about a real-world entity must reference an evidence anchor from the supplied evidence ledger. If you cannot back a claim with a real evidence_id, do not make the claim.

6. **Persuasion / resistance must come from supplied data.** Do not invent persuasion drivers or objections. Use the rolled-up `top_persuasion_drivers` and `top_objections` blocks supplied in the input — those came from the actual simulation responses.

7. **Evidence anchors must be real.** Every UUID in `evidence_anchors` must reference an item from the supplied `evidence_bundles`. Anchors not in the supplied bundles will fail validation.

8. **`factual_claims` is OPTIONAL — emit ONLY when quoting verbatim.** A `factual_claim` is for VERBATIM real-world facts: a competitor's pricing string, a public review quote, a category-language phrase. The `source_excerpt` MUST appear verbatim (whitespace-normalized, case-insensitive) in the bound evidence's content — if it doesn't, the claim will be rejected and the entire response invalidated. **Do NOT emit factual_claims for descriptive synthesis** ("the supplied target segment is X", "the price is Y") — those are subjective summary, not factual quotations. Subjective summary lives in `summary` and does not need a claim row. **When in doubt, emit `factual_claims: []`.**

## Strict response shape

Return ONE JSON object with EXACTLY these top-level keys:

```json
{
  "public_opinion_sentiment": {
    "summary": "string — subjective prose, 2-4 sentences",
    "evidence_anchors": ["uuid", "..."],
    "simulation_references": [{"kind": "agent_response", "target_id": "uuid", "note": "string|null"}],
    "confidence": "thin|moderate|clear",
    "validator_notes": []
  },
  "persuaded": {
    "summary": "string",
    "evidence_anchors": ["uuid"],
    "simulation_references": [{"kind": "agent_response", "target_id": "uuid", "note": "string|null"}],
    "confidence": "thin|moderate|clear",
    "validator_notes": [],
    "factual_claims": [/* OPTIONAL: empty unless quoting verbatim — see rules below */]
  },
  "not_persuaded": {
    "summary": "string",
    "evidence_anchors": ["uuid"],
    "simulation_references": [...],
    "confidence": "thin|moderate|clear",
    "validator_notes": [],
    "factual_claims": []
  },
  "market_acceptance_requirement": {
    "summary": "string — what the society seemed to need; reference missing_evidence",
    "evidence_anchors": ["uuid"],
    "simulation_references": [...],
    "confidence": "thin|moderate|clear",
    "validator_notes": [],
    "factual_claims": []
  }
}
```

Return ONLY the JSON object — no prose, no markdown fences.

## How to think about each section

- `public_opinion_sentiment`: read the supplied `final_stance_distribution` and `round_progression`. Describe the arc the society moved through (curious → split / resistant / receptive). Reference 2-4 evidence_anchors that ground the dominant theme.

- `persuaded`: read `top_persuasion_drivers`. Frame as "agents who became more receptive seemed to do so because…". Reference the agent_response ids those drivers came from. If a driver references a competitor or pricing as a contrast, it MUST become a `factual_claim` bound to evidence.

- `not_persuaded`: read `top_objections`. Frame as "the strongest resistance appeared to come from agents who…". Reference agent_response ids. Same factual_claim discipline applies.

- `market_acceptance_requirement`: read `trust_barrier_evidence`, `market_acceptance_evidence`, AND `missing_evidence_summary`. Describe ONE thing the society seemed to need; if that thing is "evidence we don't have" (e.g. real merchant case studies), say so and reference the missing-evidence ids in `simulation_references` (kind=evidence_item).
