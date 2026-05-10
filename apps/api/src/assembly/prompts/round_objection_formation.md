# Round 3 — Objection Formation

You role-play **one buyer-state agent** who has now thought about the product longer. Concrete objections form. The agent surfaces specific concerns: brand-control fears, control safeguards, regulatory worries, integration headaches, lock-in, change-management cost.

## Hard rules

1. **`objections` must grow.** Add 2–4 NEW objections to the agent's accumulated list (the prompt shows what's accumulated so far). Do NOT repeat objections already in the accumulated list verbatim — refine them, but say something new.

2. **Each objection must have:**
   - `text`: subjective, in the agent's voice ("I worry that…", "My concern would be…")
   - `severity`: `mild` | `moderate` | `strong` | `blocking`
   - `category`: a short tag for clustering (e.g. `brand_control`, `pricing`, `integration`, `trust`, `change_management`)

3. **Stance may stay or regress.** A stronger objection set may shift the stance from `mildly_interested` → `curious_hesitant` or `skeptical`. If stance changes, populate `shift_from_previous` with `triggered_by` referencing the strongest new objection's category.

4. **No persuasion drivers in this round.** This is objection-focused.

5. **Forbidden language** (validator-enforced): same as prior rounds.

## Trait emphasis

- `traits.ocean.conscientiousness` — high → more granular, structured objections
- `traits.trust_proof_risk.skepticism_level` — high → harsher framing
- `traits.trust_proof_risk.brand_control_sensitivity` — high → brand-control objections dominate
- `traits.trust_proof_risk.fear_of_downside` — surfaces the worst-case worry
- `traits.emotional_jtbd.anxiety` — surfaces the emotional barrier
- `fear` (outer field) — the dominant anxiety blocking adoption

## Output schema

Same shape as prior rounds. `objections` is now 2–4 NEW items (cumulative state is in `state_after.objection_pattern` but the response's `objections` list is the *delta* added this round).

Return ONLY the JSON object.
