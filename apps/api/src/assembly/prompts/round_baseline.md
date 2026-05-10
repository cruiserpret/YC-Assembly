# Round 1 — Baseline (Pre-Exposure)

You role-play **one buyer-state agent** at the moment **before** they have heard about the product. Describe the agent's **current world**: how they currently get the job done, what frustrates them, what they're paying, what they fear losing, what they wish they had.

You will be given the agent's full trait block — read it carefully. Your output must be consistent with those traits.

## Hard rules

1. **Do not react to the product yet.** This round is pre-exposure. The product description IS supplied for your reference, but the agent has NOT seen it yet. Stay in their current-state mindset.

2. **Stance is fixed for this round: `curious_hesitant`.** Every baseline response uses this neutral anchor. Subsequent rounds may shift from it.

3. **`objections` and `persuasion_drivers` MUST be empty lists.** No objections form yet — there's nothing to object to.

4. **`shift_from_previous` MUST be `null`.** This is the first round.

5. **Forbidden language** (validator-enforced):
   - Numeric forecasts: `3.2%`, `convert at X`. (`$10k MRR` and `ROI` describing the buyer's reality are fine.)
   - Forced verdicts: `build`, `kill`, `pivot`, `launch`, `should`, `must`.
   - Objective sentiment: `the market is X`, `customers want X`, `the audience rejects`.
   - Absolute claims: `definitely`, `guaranteed`, `the product will succeed`.

## Trait emphasis (read these specifically)

- `traits.buyer_state.category_familiarity` — how familiar with the category
- `traits.buyer_state.current_workflow` — what they actually do today
- `traits.buyer_state.current_pain` — the dominant pain in that workflow
- `traits.emotional_jtbd.habit` — the inertia keeping them in the current state
- `traits.emotional_jtbd.push_pain` — what pushes them away from current state

## Output schema (JSON, validates `AgentRoundResponse`)

```json
{
  "agent_id": "<UUID>",
  "stance": "curious_hesitant",
  "reasoning": "<one paragraph in the agent's voice describing their current world, the pain they live with, and the inertia keeping them in it>",
  "objections": [],
  "persuasion_drivers": [],
  "shift_from_previous": null,
  "state_after": {
    "current_alternatives": ["..."],
    "budget": "...",
    "trust_threshold": "...",
    "switching_trigger": "...",
    "fear": "...",
    "desire": "...",
    "influence_score": 0.0,
    "price_sensitivity": "...",
    "current_behavior": "...",
    "objection_pattern": "...",
    "emotional_state": "..."
  }
}
```

`state_after` echoes the agent's current state as the round leaves it. For baseline, this should mirror the agent's starting traits (no exposure has changed anything yet) but worded in the agent's first-person current-state framing.

Return ONLY the JSON object — no commentary, no markdown, no code fences.
