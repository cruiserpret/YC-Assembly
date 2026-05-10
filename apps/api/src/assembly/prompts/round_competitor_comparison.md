# Round 4 — Competitor / Alternative Comparison

The agent now consciously weighs the product against their **current alternatives** (named in `state_after.current_alternatives`) and any **named competitors** in the brief. Pricing, ROI math, switching cost, integration cost, control trade-offs all surface here.

## Hard rules

1. **Use ONLY named competitors and alternatives.** Do NOT invent competitor names, pricing, or features the brief did not supply. If the brief is sparse, say so subjectively ("the agent has no clear alternative in mind for X").

2. **Pricing language stays qualitative.** Say "cheaper than the agent's current freelancer retainer" — do NOT invent dollar amounts the brief did not state. Buyer-vocabulary mentions of `MRR`, `ROI`, `$10k–$80k` are fine when they appear in the agent's own state. Forecasts (`3.2% conversion lift`) are forbidden.

3. **`objections` may grow 0–2 NEW items focused on price/comparison/switching cost.** `persuasion_drivers` may grow 0–2 NEW items focused on what the alternative does WORSE.

4. **Stance may shift in either direction.** Populate `shift_from_previous` if stance changes; `triggered_by` should reference the comparison axis (free-text label like `"competitor_comparison_pricing_axis"` or an evidence_id from `evidence_items` describing competitor pricing).

## Trait emphasis

- `traits.economic.willingness_to_pay`, `cost_of_current_alternative`, `roi_expectation`, `time_to_value_expectation`
- `traits.economic.purchase_authority` — sole vs shared changes the framing
- `current_alternatives` (outer) and `switching_trigger`
- `price_sensitivity` (outer)

## Output schema

Same as prior rounds. New objections / persuasion_drivers are the delta added this round.

Return ONLY the JSON object.
