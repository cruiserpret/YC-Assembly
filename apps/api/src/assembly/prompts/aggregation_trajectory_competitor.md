# Phase 7 — Aggregation Call B: Product Trajectory + Competitor Analysis

You are writing TWO sections of the Assembly synthetic-society report.

## Sections you produce

1. `product_trajectory` — How the product appeared to move across the seven simulation rounds.
2. `competitor_analysis` — How the simulated society compared the product against its named competitors and current alternatives.

## Hard rules — non-negotiable

1. **Subjective framing only.** Same as Call A — never "the market is X", never "customers want X".

2. **No verdicts.** No build / kill / pivot / launch / abandon / scrap.

3. **No real-world instructions.** No ads / landing pages / validation tests / spend instructions.

4. **No fake numbers.** No CTR / CAC / LTV / MRR / conversion rate / dollar forecasts. The simulation entropy, separation ratio, and stance-bucket counts in the supplied data ARE legitimate (they describe the simulated society itself). Quote those if useful.

5. **No invented competitors.** `competitor_analysis.competitors[*].competitor_name` MUST be a name that appears in `competitor_evidence` or in the brief's competitor list. Do NOT invent competitors. Do NOT invent pricing for competitors. Do NOT invent reviews of competitors.

6. **Every competitor claim about pricing, features, reviews, or quotes MUST be a factual_claim bound to evidence.** The `source_excerpt` MUST appear verbatim (whitespace-normalized, case-insensitive) in the bound evidence's content. If you cannot quote verbatim, do NOT emit a factual_claim — describe the comparison subjectively in `comparison_summary` instead.

   **`factual_claims` is OPTIONAL.** Emit ONLY when quoting verbatim from a real evidence row. Do NOT emit factual_claims for synthesis ("agents seemed to view X as cheaper") — that is subjective interpretation, not a factual quotation. **When in doubt, emit `factual_claims: []`.**

7. **Trajectory uses simulation state, not invention.** The `round_progression` block supplied to you is the ground truth. Describe what shifted, when, and why — do not invent shifts that aren't in the data.

## Strict response shape

```json
{
  "product_trajectory": {
    "summary": "string — describe the arc across 7 rounds; reference round numbers",
    "evidence_anchors": ["uuid"],
    "simulation_references": [
      {"kind": "simulation_round", "target_id": "uuid", "note": "string|null"},
      {"kind": "agent_response", "target_id": "uuid", "note": "string|null"}
    ],
    "confidence": "thin|moderate|clear",
    "validator_notes": []
  },
  "competitor_analysis": {
    "summary": "string — overall competitive context",
    "evidence_anchors": ["uuid"],
    "simulation_references": [...],
    "confidence": "thin|moderate|clear",
    "validator_notes": [],
    "competitors": [
      {
        "competitor_name": "string — must come from supplied competitor_evidence",
        "comparison_summary": "string — subjective: 'agents seemed to view X as...'",
        "evidence_anchors": ["uuid"],
        "factual_claims": [
          {
            "text": "string — factual claim about competitor pricing/features/reviews",
            "source_evidence_id": "uuid",
            "source_excerpt": "string — must appear verbatim in the source",
            "claim_type": "support|observation|contradiction",
            "basis": "direct|analogical",
            "confidence": 0.0
          }
        ]
      }
    ]
  }
}
```

Return ONLY the JSON object.

## How to think about each section

- `product_trajectory`: walk through `round_progression`. For each round of interest, note the dominant stance shift and what the supplied data indicates triggered it (objections forming, proof exposure, social influence). Reference simulation_round ids and a few agent_response ids that exemplify the shift.

- `competitor_analysis`: for each competitor in `competitor_evidence`, write a short subjective comparison ("agents seemed to view Shopify Magic as…"). If the supplied `pricing_evidence` includes pricing rows linked via `priced_against` edges, you may surface that comparison — but every pricing claim becomes a factual_claim bound to the pricing evidence row. If the supplied data has no pricing for a competitor, do NOT invent one — say "supplied evidence did not include pricing for X".
