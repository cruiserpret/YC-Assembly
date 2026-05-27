# Roadmap: Competitor-Heavy Market Mode + Gate Recovery UX

**Status: roadmap item, not yet scheduled.** Phase number to be assigned by operator.

**Classification: product reliability + report-quality fix. NOT a model-accuracy change.**

## Context

During Phase 12E.5Q, the Kelviq paid simulation aborted before persona generation because the runtime gate `competitor_user_share = 0.62 > 0.60` fired. The brief-quality audit had warned this would happen with HIGH risk band ahead of launch. Cost: $0. Prediction: not produced.

For the **validation harness**, this is correct behavior. The case shouldn't count as a clean validation run because persona populations dominated by rival-product-user voices systematically bias buyer / receptive proportions downward. The gate caught a real structural issue (the MoR category is genuinely competitor-saturated: Paddle, Lemon Squeezy, FastSpring, Chargebee + composed Stripe Billing + Stripe Tax).

For the **customer-facing product**, returning no output to a founder would be a bad experience. If Kelviq's founder were using Assembly to inform a real decision, "your market is too competitor-heavy for us to simulate, here's nothing" is the wrong answer. They'd lose trust in the product.

## The distinction (load-bearing)

| Surface | Behavior on gate trip | Why |
|---|---|---|
| **Validation harness** (`phase_12a_10c_repeatability_harness.py`) | Hard abort. Run is failed. Does not count. | Validation must measure clean cases only; biased simulations contaminate fidelity scoring |
| **Customer-facing product** (founder-facing report path) | Degrade gracefully: warn, offer alternative run modes, produce a useful decision report | A founder should always leave Assembly with directional market understanding, even when the market is structurally messy |

These are different consumers of the same engine. The fix is at the orchestration / report layer, NOT at the gate threshold.

## Anti-overfit guardrails (carry-forward, non-negotiable)

- Do NOT raise the global `competitor_user_share <= 0.60` gate threshold. The gate stays.
- Do NOT weaken validation discipline. Validation harness keeps the hard abort.
- Do NOT make Kelviq pass artificially. Kelviq stays in the "does not count" ledger.
- Do NOT change persona-generation logic, intent routing, or source-audience profile weights to accommodate competitor-heavy markets globally. The fix is downstream / UX-side, not in the prediction engine.

## Customer-facing fallback design

When the gate would trip in the customer-facing path:

### 1. Show a competitor-dominance warning

Friendly, founder-readable copy:

> "The market for this category is heavily defined by named incumbents (e.g. Paddle, Lemon Squeezy, FastSpring). N% of plausible voices we modeled are already loyal to one of these. Your launch will be reacted to mostly through the lens of those incumbents."

The warning includes a count of named incumbents detected and the share of competitor-user voices.

### 2. Explain WHY the category is competitor-heavy

Surface the structural reason, not just the number:

> "When a category is dominated by ≥4 well-known incumbents and your target customers naturally reference them, most early reaction will be comparison-shopping ('how is this different from X?') rather than fresh evaluation. This is a market-structure observation, not a problem with your product."

### 3. Offer multiple run modes

| Mode | Description | When to use |
|---|---|---|
| **Competitor-heavy market simulation** | Run as-is, with `competitor_user_share` allowed up to a configurable ceiling (e.g. 0.75). Output explicitly tagged as "competitor-heavy view." | Founder wants to see how they'd be received in the messy real market |
| **Balanced target-market simulation** | Force competitor_user_share down to ≤0.40 by upweighting target_customer_evaluator and proof_seeker_only voices. Output tagged "balanced view." | Founder wants to see how their actual ICP would react, separated from incumbent gravity |
| **Source-audience simulation** | Standard source-audience injection at the selected `launch_source` profile (HN / PH / etc.). Output tagged "source-audience view." | Founder wants the social-platform-flavored reaction (HN crowd, PH crowd, etc.) |
| **Side-by-side comparison** | Run two or three of the above and present distributions side by side, with confidence-band caveats. | Founder wants the full picture of where their market sits |

Default for competitor-heavy markets: side-by-side comparison of competitor-heavy + balanced + source-audience, so the founder sees the variance the market structure introduces.

### 4. Allow founder to broaden or edit the target audience

In the report (or pre-run UI):

- Show the named incumbents the brief implies
- Suggest "broaden one or more of these to a category descriptor (e.g. 'subscription-billing tools' instead of 'Paddle')"
- Suggest target_customer rewrites that reduce competitor anchoring
- Offer a one-click "re-run with broader audience" using suggested edits
- Brief edits create a new brief hash; the new run is tracked separately

### 5. Clearly mark the confidence limits

Every run that fired the gate-recovery path must show:

- "Competitor-heavy market — confidence band: WIDE"
- Explicit MAE band annotation (e.g. "expect ±10–15 pp on receptive / uncertain in this kind of market")
- Reminder that incumbent-comparison patterns dominate the reaction
- Side-by-side comparison if applicable, with bucket deltas highlighted

### 6. Do NOT count the run as clean validation

- Any run that fired the gate-recovery path is marked `gate_recovery=true` in the per-run audit artifact
- Such runs do not contribute to MAE / TVD scoring against locked labels
- They do not increment the source-profile's `validation_support_count`
- They do appear in founder reports with full confidence-band caveats
- Operator can still export the prediction for offline inspection, but it's labeled "advisory output, not a validation case"

## Report content additions (when gate-recovery mode fires)

Beyond the distributions themselves, the founder report under gate-recovery mode adds these sections:

### Competitor dominance warning

Lead paragraph that names the incumbents and explains the structural lens. Surfaces the `competitor_user_share` value and the `named_incumbents_count` from the brief-quality audit.

### Incumbent comparison map

Per-named-incumbent breakdown:
- which persona cohorts mention this incumbent
- their stance distribution (skeptical / loyal / migrating)
- top stated objections to switching FROM this incumbent
- top conditions that would trigger a switch

### Market-entry risk note

Explicit acknowledgment of category structure risk:
- "This category has ≥N established players with strong workflow lock-in"
- "Expect early reaction to compare-and-question rather than buy-and-try"
- "Mass adoption signals will likely be muted in the first wave; receptive-band founders typically see N% buyer, M% receptive in similar markets"

### "What would reduce incumbent lock-in?"

Synthesized from agents' stated switching conditions:
- top 5 features / proof points that personas said would let them switch
- top 5 frustrations with named incumbents (sourced from evidence snapshot, not from product)
- pricing / contract / migration friction blockers

### "Who is NOT already loyal to competitors?"

Subset analysis on the non-competitor-user voices:
- their cohort composition
- their stated needs
- which incumbent-loyal voices might be persuadable vs hard-resistant
- estimated TAM of "no current solution" + "weakly attached" segments

### Recommended audience rebalance

Concrete brief edits Assembly recommends if the founder wants a balanced re-run:
- "Drop named incumbents from N=5 to N=3"
- "Rewrite target_customers L3 + L5 to remove competitor anchoring"
- "Add 2 broad alternatives (e.g. 'homegrown approach', 'no current solution')"
- One-click "apply suggested edits and re-run" if UI supports it

## Goal

Assembly should never silently fail on a messy real product. It should:
1. Identify that the market is messy.
2. Explain why in plain language.
3. Still produce a useful decision report.
4. Be honest about confidence limits.
5. Keep validation discipline separate from product UX.

## What this is NOT

- NOT a model-accuracy improvement. The prediction engine is unchanged; this is an orchestration + report layer addition.
- NOT a gate threshold change. The 0.60 cap stays.
- NOT a per-product calibration. This is generic recovery UX that any competitor-heavy market triggers.
- NOT a path to count gate-recovery runs as validation. Validation ledger remains untouched.

## Integration points (when scheduled)

- **Orchestration layer** ([`apps/api/src/assembly/orchestration/live_founder_brief.py`](../src/assembly/orchestration/live_founder_brief.py)): branch when the persona-quality gate would trip; instead of aborting, dispatch to gate-recovery run modes.
- **Founder report schema** ([`apps/api/src/assembly/schemas/`](../src/assembly/schemas/)): add `gate_recovery: bool`, `gate_recovery_reason: str`, and the new report sections (competitor dominance warning, incumbent comparison map, market-entry risk note, switch-conditions analysis, audience-rebalance recommendations).
- **Run mode dispatcher**: new module `apps/api/src/assembly/orchestration/run_modes.py` (does not exist yet) to encode `{competitor_heavy, balanced, source_audience, side_by_side}` mode selection and the per-mode profile overrides.
- **Brief-quality preflight** (already documented in [`phase_12e5r_preflight_discipline.md`](../_audit/proof_packets/phase_12e5r_preflight_discipline.md)): when this UX ships, the operator-loop preflight discipline applies only to validation-harness launches; customer-facing path goes through gate-recovery instead.

## Open questions for operator before scheduling

1. **Configurable ceiling** for the competitor-heavy mode: 0.75? 0.80? unbounded? Default proposal: 0.75 (still flags as "very competitor-heavy" but allows simulation to run).
2. **Default mode selection** when gate-recovery fires: always side-by-side? or competitor-heavy by default with link to alternatives? Default proposal: side-by-side (most informative).
3. **MAE band annotations**: are these computed from historical validation cases per category, or hardcoded? Default proposal: hardcoded conservative bands (±10–15 pp) in V0; per-category bands in V1 after sufficient validation cases land.
4. **Brief-edit suggestion mechanism**: rule-based (drop the N+1 named incumbent if N≥4) or LLM-suggested? Default proposal: rule-based in V0 to keep it deterministic; LLM-suggested in V1 with operator-review-before-apply.
5. **Phase number assignment**: where does this slot? Operator decides. Suggested: after the 5-product validation checkpoint, before the Behavioral Agent Mind Layer (since the BAML changes how persona priors work, which would interact with this UX).

## Sequencing relative to other roadmap items

```
current → 5-product validation checkpoint
   │
   ├── Files.md (scored, pass)
   ├── DocuSeal v2 (scored, serious miss)
   ├── Opslane (scored, pass)
   ├── Naptick AI (next, PH#1)
   └── Candidate 5 (TBD, ≥1 non-HN source)
        │
        ▼
[checkpoint reached → continue major phases in parallel with ongoing validation]
        │
        ├── Competitor-Heavy Market Mode (THIS DOC) — product reliability
        │       │
        │       └── ships customer-facing gate recovery + report sections
        │
        ├── Behavioral Agent Mind Layer — model-accuracy
        │       │
        │       └── trust threshold, risk tolerance, loss aversion, ...
        │
        └── Phase 12D Market Data Ingestion Priors — model-accuracy
```

Both customer-reliability and model-accuracy tracks proceed in parallel after the validation checkpoint. They're separable concerns: the Competitor-Heavy Market Mode does not change predictions, and the Behavioral Agent Mind Layer does not change what happens when gates trip.

## What does NOT change

- The validation harness still aborts on gate trip (hard discipline).
- The 0.60 `competitor_user_share` threshold stays.
- The brief-quality preflight rule for validation launches stays.
- Kelviq stays in the "does not count" ledger.
- Predicted distributions when the gate doesn't trip are unchanged.
- Files.md, DocuSeal v2, Opslane validation results are unaffected.
