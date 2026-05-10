# Round 5 — Proof / Clarification Exposure

The agent now sees **proof material** synthesized from the evidence ledger — direct evidence (user-supplied + fetched competitor pages), analogical evidence (category-language phrases), and explicitly missing evidence. This round determines whether the agent's `trust_threshold` is **cleared** or remains.

## Hard rules

1. **Use ONLY supplied evidence excerpts.** Do NOT invent customer quotes, case studies, statistics. Every persuasion driver added in this round MUST reference an `evidence_anchor` UUID from the supplied ledger. If no proof for a given concern was supplied, reflect that — the agent's `trust_threshold` stays unmet for that concern.

2. **Stance may shift toward `mildly_interested` / `strongly_interested`** ONLY if the agent's `trust_threshold` is qualitatively cleared by the proof. If proof is sparse or addresses different concerns, stance may stay or even regress.

3. **`shift_from_previous.triggered_by`** must reference either an `evidence_item.id` OR a free-text label like `"proof_exposure_addressed_brand_control_concern"`. No bare shifts.

4. **`persuasion_drivers` may grow 0–3 NEW items** anchored to specific evidence. `objections` may shrink (note in reasoning) but the response's `objections` list is the *new* objections (usually 0).

5. **Forbidden language** (validator-enforced): same as prior rounds.

## Trait emphasis

- `traits.trust_proof_risk.proof_requirement` — what proof artifact this agent requires
- `traits.trust_proof_risk.required_credibility_signal` — what specifically must be visible
- `traits.trust_proof_risk.skepticism_level` — high → hard to clear
- `traits.ocean.conscientiousness` — high → demands granular proof
- `trust_threshold` (outer) — the qualitative bar

## Output schema

Same as prior rounds. Persuasion drivers added this round MUST have non-empty `evidence_anchors` referencing real evidence_item UUIDs from the supplied ledger.

Return ONLY the JSON object.
