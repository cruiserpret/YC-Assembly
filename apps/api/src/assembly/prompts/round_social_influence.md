# Round 6 — Social Influence (Pairwise Debate)

You role-play **one buyer-state agent** ("the subject") in a single moment of debate with a **peer agent**. The peer's stance and reasoning from round 5 are supplied. You decide whether the peer's argument shifts the subject's stance.

This prompt is invoked **once per (subject, peer) pair**. Each invocation produces ONE `DebateTurnOut`. The engine collects all turns and applies caused_shifts to the subject afterwards.

## Hard rules

1. **You are simulating a single debate turn.** Output is one `DebateTurnOut`, not a full agent response. Stance for the agent is updated only via `caused_shifts`.

2. **`caused_shifts` is 0 or 1 entries.** If the subject's stance does NOT change, return `caused_shifts: []`. If it does change, return one entry with `from_stance`, `to_stance`, `reason` (subjective; tied to peer's argument), `triggered_by` (a free-text label or peer's reasoning excerpt).

3. **The peer's argument should be summarized by you in `argument`.** Use the peer's reasoning + their top persuasion driver. ≤ 200 words.

4. **Use the subject's susceptibility traits.** A subject with `low susceptibility_to_peer_shift` should rarely shift; with `high`, more readily. Same for `agreeableness`, `status_sensitivity`, `word_of_mouth_likelihood`. These are explicitly named in the data block — read them and let them shape the response.

5. **Forbidden language** (validator-enforced): same as prior rounds. NO forced verdicts (`build`, `kill`, `pivot`). NO objective sentiment.

## Trait emphasis (read the SUBJECT's traits, not the peer's, to decide the shift)

- `susceptibility_to_peer_shift` — high → shifts more readily
- `traits.ocean.agreeableness` — low → pushes back on peer
- `traits.ocean.extraversion` — high → engages more in debate (longer `argument`)
- `traits.social_influence.status_sensitivity`, `word_of_mouth_likelihood`

## Output schema (DebateTurnOut)

```json
{
  "speaker_agent_id": "<peer's UUID>",
  "target_agent_id": "<subject's UUID>",
  "responding_to_turn_id": null,
  "argument": "<≤ 200 words summarizing the peer's argument from round 5>",
  "caused_shifts": [
    {
      "from_stance": "skeptical",
      "to_stance": "curious_hesitant",
      "reason": "<one-sentence subjective reason>",
      "triggered_by": "<peer's reasoning excerpt or free-text label>"
    }
  ]
}
```

Return ONLY the JSON object.
