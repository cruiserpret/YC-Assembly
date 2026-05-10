# Round 7 — Final Stance

The agent commits to a **single final stance** synthesized from everything they've experienced so far: their starting traits, their first reaction, the objections they formed, the competitor comparison, the proof they were exposed to, and the social influence from peers.

## Hard rules

1. **Stance is final and from the closed set:** `strongly_interested`, `mildly_interested`, `curious_hesitant`, `confused`, `skeptical`, `resistant`. No free-text.

2. **No new `objections`.** Only re-state the agent's strongest **unresolved** objection if any (1 item, or empty).

3. **No new `persuasion_drivers`.** Only re-state the strongest persuasion driver that influenced the final stance (1 item, or empty). Must reference a real `evidence_anchor` UUID if it survived from prior rounds.

4. **`shift_from_previous`** is REQUIRED if the final stance differs from the round-6 state. Set `triggered_by` to a synthesis label like `"final_synthesis_after_proof_and_debate"` or to the strongest single trigger.

5. **`reasoning` is one paragraph in the agent's voice** explaining why this stance is the final position. Must reference at least one trait field by name (e.g., "given my low risk tolerance" or "my switching trigger requires…").

6. **Forbidden language** (validator-enforced): same as prior rounds. No forecasts, no verdicts, no objective sentiment.

## Trait emphasis

- All six layers — this is the synthesis round. Read each layer's relevant fields and let the agent reason from them.
- `switching_trigger` (outer) — match against what proof exposure delivered.
- `traits.trust_proof_risk.fear_of_downside` — if unresolved, blocks `mildly_interested`+.
- `traits.emotional_jtbd.desired_transformation` — what the agent hopes to become; if pulled, leans toward interested.

## Output schema

Same as prior rounds. `state_after` is the final state; subsequent rounds do not exist.

Return ONLY the JSON object.
