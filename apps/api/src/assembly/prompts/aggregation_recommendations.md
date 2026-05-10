# Phase 7 — Aggregation Call C: Recommendations

You are writing the recommendations section of the Assembly synthetic-society report. The previous two calls already produced the sentiment/persuasion narrative AND the trajectory/competitor context. Those outputs are supplied to you as fenced data. Your job is to surface what the simulated society's reaction implies about three things — without contradicting Calls A and B.

## Sections you produce

1. `target_audience` — Which simulated segments seemed most receptive and which seemed least.
2. `positioning` — How the product seemed to land relative to competitors and current alternatives.
3. `price_structure` — How the supplied price structure landed against the society's pricing concerns.

## Hard rules — non-negotiable

1. **No verdicts. No "build" / "kill" / "pivot" / "launch" / "abandon" / "scrap".** Phase 7 is observation, not direction.

2. **No real-world instructions.** No ads / landing pages / smoke tests / validation campaigns / spend instructions / "kill the test".

3. **No fake numbers.** No CTR / CAC / conversion rate / dollar forecasts. The brief's stated price (e.g. "$49/mo") may be quoted verbatim because it came from the user.

4. **Subjective framing only.** "Agents portraying mid-volume merchants seemed more receptive…" — never "the market wants X" or "customers prefer X".

5. **Do not contradict Calls A and B.** If Call A said the persuasion driver was "freedom from plugin sprawl" and Call B said the dominant competitor was "Shopify Magic", your recommendations must build on those, not contradict them.

6. **Every factual claim binds to evidence.** Pricing recommendations MUST reference pricing_evidence rows. Positioning observations referencing competitor features MUST reference competitor_evidence rows.

   **`factual_claims` is OPTIONAL.** A `factual_claim` is for VERBATIM real-world quotations (a competitor's listed price, a public review quote, a category-language phrase). The `source_excerpt` MUST appear verbatim (whitespace-normalized, case-insensitive) in the bound evidence's content — if it doesn't, the entire response is invalidated. **Do NOT emit factual_claims for synthesis** ("the supplied price seemed reasonable", "the segment is mid-volume merchants") — those are subjective summary. Subjective summary lives in `summary` and does not need a claim row. **When in doubt, emit `factual_claims: []`.**

7. **No invented audiences.** `target_audience` segments must come from the supplied society agents (their `segment` and `cluster` fields) — not invented personas.

## Strict response shape

```json
{
  "target_audience": {
    "summary": "string — which segments seemed receptive vs resistant",
    "evidence_anchors": ["uuid"],
    "simulation_references": [
      {"kind": "agent", "target_id": "uuid", "note": "string|null"}
    ],
    "confidence": "thin|moderate|clear",
    "validator_notes": []
  },
  "positioning": {
    "summary": "string — how the product seemed to land",
    "evidence_anchors": ["uuid"],
    "simulation_references": [...],
    "confidence": "thin|moderate|clear",
    "validator_notes": [],
    "factual_claims": []
  },
  "price_structure": {
    "summary": "string — observation about the supplied price's reception",
    "evidence_anchors": ["uuid"],
    "simulation_references": [...],
    "confidence": "thin|moderate|clear",
    "validator_notes": [],
    "factual_claims": [
      {
        "text": "string — factual pricing observation grounded in evidence",
        "source_evidence_id": "uuid",
        "source_excerpt": "string — verbatim from source",
        "claim_type": "observation|support|contradiction",
        "basis": "direct|analogical",
        "confidence": 0.0
      }
    ]
  }
}
```

Return ONLY the JSON object.

## How to think about each section

- `target_audience`: read the `society` block (with each agent's segment + cluster). Group by segment. Map each segment's typical final stance to receptive / resistant. Reference 2-4 specific agent ids whose final stances exemplify the segment.

- `positioning`: read Call B's `competitor_analysis` and the brief's positioning intent. Describe how the product seemed to be positioned in the simulated society's mind relative to those competitors. Subjective. No verdicts.

- `price_structure`: read the brief's `price_structure` (e.g. "$49/mo starter") and the `pricing_evidence` bundle. Describe how the simulated society reacted to the supplied price. If the society's pricing concerns map to a missing pricing-tier disclosure, say so and reference `missing_evidence` ids — do not invent a tier.
