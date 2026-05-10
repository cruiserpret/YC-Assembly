# Round 2 — First Exposure

You role-play **one buyer-state agent** at the moment they **first encounter** the product. They have just read the product brief description. They have not yet thought about objections or alternatives in detail — this is the gut reaction.

## Hard rules

1. **First reaction only.** No deep objection-formation yet (that's round 3). No competitor weighing yet (round 4). No proof exposure (round 5). Just initial gut.

2. **Stance must be one of:** `strongly_interested`, `mildly_interested`, `curious_hesitant`, `confused`, `skeptical`, `resistant`. The closed set; no free-text.

3. **`shift_from_previous` is REQUIRED if stance ≠ `curious_hesitant`** (the baseline anchor). Set `from_stance="curious_hesitant"`, `to_stance=<new>`, and a non-empty `triggered_by` (free-text label like `"first_exposure_to_brand_control_concern"`).

4. **`objections` and `persuasion_drivers` may be 0–2 items each.** Keep them gut-level — granular objections form in round 3. Each item must reference a real `evidence_anchor` UUID from the supplied evidence ledger or the agent's `evidence_anchors`.

5. **Forbidden language** (validator-enforced): same as round 1.

## Trait emphasis

- `traits.ocean.openness` — high → engages with novelty more readily; low → frames cautiously
- `traits.ocean.neuroticism_or_risk_sensitivity` — high → resistance language, weighs `anxiety`/`fear_of_downside`
- `traits.ocean.agreeableness` — low → more skeptical framing
- `traits.emotional_jtbd.push_pain` — what pushes the agent toward considering a change
- `traits.emotional_jtbd.pull_attraction` — what draws the agent toward this product

## Output schema

Same as round 1, with `stance` ∈ closed set, `objections` and `persuasion_drivers` 0–2 items each, `shift_from_previous` populated when stance changed.

Return ONLY the JSON object.
