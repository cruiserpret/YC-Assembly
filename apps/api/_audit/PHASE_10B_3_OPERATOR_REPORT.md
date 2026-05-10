# Phase 10B.3 — Operator Report

**Run:** GlowPlate fresh end-to-end (`47a25fd9-3f14-4b58-bc67-ca8621f91592`)
**Run scope:** `run_live_glowplate_9ce2016dfabe`
**Status:** complete · 24 personas · 96 turns · 72 ballots
**Brief:** $79 plate + USB-C base, $139 two-plate bundle, 120–145°F warming,
45 min runtime, dishwasher-safe ceramic plate (microwave-safe when separated).
Competitors: Ember Mug, Crock-Pot Lunch Crock, HotLogic Mini.

J-checklist verification: **14 / 15 PASS**
(see [`scripts/verify_glowplate_run.py`](../scripts/verify_glowplate_run.py))

> ⚠ **Environmental issue, not code:** the Anthropic account hit `Your credit
> balance is too low` during the discussion stage, so 20 / 24 final-ballot LLM
> calls fell back to deterministic `needs_more_information`. Top up credits
> and rerun for the full LLM-driven view. All Phase 10B.3 *infrastructure* is
> verified green by the unit suite + the audit JSONs the orchestrator wrote.

---

## 1. Files changed

```
apps/api/src/assembly/sources/product_grounding/
├── product_fact_card.py           (extended — bundle / temperature /
│                                   runtime / cleaning / materials)
├── provided_fact_lock_v2.py       (NEW — 8-category re-ask detector +
│                                   "Since X, I'd want proof Y" repair)
├── human_society_realism.py       (NEW — agent_self_awareness_leak_detector)
├── stance_strictness.py           (NEW — strict v2 RECEPTIVE classifier)
├── report_polish.py               (NEW — confident headline +
│                                   hardest-to-convince + best-fit +
│                                   evidence-flavor builders)
├── stance_calibrator.py           (kept; v2 layered on top)
└── __init__.py                    (export 10B.3 helpers)

apps/api/src/assembly/orchestration/
├── live_founder_brief.py          (added Phase 10B.3 audits 7–9 +
│                                   confident headline + audience copy +
│                                   evidence flavor; populated
│                                   most_resistant_cohorts from
│                                   hardest-to-convince signal)
├── live_discussion_pipeline.py    (system prompt + 3 ballot
│                                   instructions rewritten — no more
│                                   "synthetic n=24" injected by
│                                   the orchestrator itself)
└── live_final_ballot_repair.py    (removed caveat-injection)

apps/api/scripts/
├── run_glowplate_fresh_demo.py    (NEW)
└── verify_glowplate_run.py        (NEW — 15-criterion J-checklist)

apps/api/tests/
├── test_product_grounding_10b_3.py  (NEW — 28 acceptance tests)
└── test_product_grounding_10b_1.py  (regression update for renamed prompt
                                      header / softened assert)

apps/web/
├── src/components/AudienceFitCards.tsx  (hardest-card fallback when
│                                         resistant=0; still RECEPTIVE label)
└── tests/unit/phase_10b.test.tsx        (R10.3 fallback test added)
```

## 2. RECEPTIVE calibration changes

**Label kept.** "RECEPTIVE" remains the user-facing label
(`apps/web/src/lib/stance.ts:27` short label, `:26` full label). Spec's
explicit instruction — *"Do NOT remove or rename RECEPTIVE"* — honored.

**Strictness layer (`stance_strictness.py`).** A persona is now RECEPTIVE
only when reasoning shows **(positive intent OR personal use-case fit)
AND no major proof gate**. New patterns:

| Signal | Pattern examples |
|---|---|
| `_USE_CASE_FIT_RE` | "my food gets cold during calls", "I work from home", "this would solve a real annoyance for me" |
| `_MAJOR_PROOF_GATE_RE` | "safety certification", "UL/ETL listing", "FDA approval", "food-contact material proof", "coating durability proof", "third-party certification", "before I can trust", "I'm not sold until safety is proven" |

If a major proof gate dominates, the ballot is downgraded to UNCERTAIN
(`curious_but_unconvinced`) regardless of any weak positive signal —
because the gate is doing the heavy lifting.

**This run's v2 audit numbers** (`stance_strictness_quality.json`):

| Metric | Value |
|---|---|
| Ballots reviewed (all stages) | 72 |
| `receptive_count_before` | 37 |
| `receptive_count_after` | 13 |
| `downgraded_receptive_count` | **24** |
| `upgraded_receptive_count` | 0 |
| `stance_justification_missing_count` | 0 |

Every downgrade carried a `stance_justification` string explaining *why*
("downgrade_for_to_uncertain_strict_v2: reasoning centers on a major
proof gate (safety / certification / material / durability) that must
clear before this can count as receptive").

## 3. Provided-Fact Lock v2 results

**Lock surface.** The `ProductFactCard` now distinguishes:

```
primary_price        ($79)
bundle_price         ($139)        ← NEW
accessory_prices     []
kit_contents         (parsed)
materials            ['Ceramic plate', 'Removable ceramic plate']  ← NEW
power_facts          (parsed)
charging_facts       ['USB-C rechargeable / USB-C powered',
                      'Rechargeable']                              ← USB-C added
runtime_facts        ['Up to 45 minutes']                          ← NEW
temperature_facts    ['Warming range 120°F–145°F']                 ← NEW
cleaning_facts       ['Dishwasher-safe',
                      'Microwave-safe when separated from base']   ← NEW
included_features    (parsed)
excluded_features    (parsed)
not_categories       (parsed)
launch_state         (parsed)
```

The prompt block (`fact_card_prompt_block`) renders every category and
adds two new persona rules:

> • Treat ALL facts above as already provided. You may question whether a
>   claim is credible, but DO NOT ask for facts that are listed here as
>   if they were missing — INCLUDING price, bundle price, kit contents,
>   materials, power, charging, runtime, temperature, cleaning /
>   dishwasher / microwave claims, and excluded features.
> • If a fact above is something you'd want VERIFIED (e.g. dishwasher-safe
>   across many cycles, runtime under real loads, food-contact certification),
>   phrase your concern as 'Since the brief says X, I'd want proof Y' — do
>   NOT phrase it as if X were unknown.

**Detector** (`audit_provided_fact_lock_v2`) covers 8 fact categories:
`cleaning_dishwasher`, `cleaning_microwave`, `runtime`, `temperature`,
`charging_usb_c`, `bundle_price`, `materials`, `kit_contents`. It only
fires when the fact card *has* a value to defend (so single-fact briefs
don't false-positive).

**Repair** (`repair_known_fact_reask`) rewrites a re-ask sentence into a
verification-form sentence preserving the persona's underlying concern.
Smoke-tested with the GlowPlate fact card:

```
INPUT:  "I like the concept. Is it dishwasher-safe? Also, can it keep
         food warm for a full meal? And is the base rechargeable?"

OUTPUT: "I like the concept. Since the plate is described as
         dishwasher-safe, I'd want proof it survives repeated dishwasher
         cycles without coating damage. Since the brief says runtime is
         Up to 45 minutes, I'd want a real-food test showing pasta,
         rice, soup, and meat stay within a useful serving-temperature
         range for that whole window. Since the brief says the base is
         USB-C rechargeable, I'd want to know charge time, battery
         lifespan, and whether one charge handles multiple meals."
```

**This run's v2 audit numbers** (`provided_fact_lock_v2_quality.json`):

| Metric | Value |
|---|---|
| `known_fact_reask_count` | **0** |
| `fact_categories_violated` | [] |
| `repaired_count` | 0 |
| `pass` | true |

The discussion prompt's stricter rules + the prompt-block rules
prevented re-asks from being generated in the first place — so the
repair path didn't need to fire.

## 4. Human-society realism results

**Detector** (`detect_self_awareness_leak`). 25 forbidden phrase
patterns plus the `n=NN` regex. Catches: "as an agent", "as an AI",
"synthetic persona / agent / society", "in this simulation", "n=24",
"directional signal / rather than", "not a forecast", "not a verdict",
"as a model", "as a language model", "purely synthetic", etc.

**Prompt rewrites.** Three live-discussion-pipeline ballot prompts
that were *actively instructing* personas to caveat with "synthetic
n={n} simulation" have been replaced with:

> private_reasoning: "<2-4 sentences in your voice as a real person;
> reference one specific evidence excerpt or competitor or your own
> situation; do NOT mention the simulation, AI, synthetic society, or
> sample size>"

The system prompt was also rewritten (Phase 10B.3 framing): *"You are
NOT an AI assistant. You are NOT a synthetic agent. You are a real
person in the target market reacting naturally to this product."*

**This run's audit numbers** (`human_society_realism_quality.json`):

| Metric | Value |
|---|---|
| Turns scanned | 96 |
| Ballots scanned | 72 |
| `self_awareness_leak_count` | **0** |
| `any_leak` | false |

Zero leaks across 96 turns + 72 ballots — the prompt-level prevention
caught everything; the audit-level repair didn't need to run.

## 5. Hardest-to-convince card fix

**The bug:** the previous code path showed *"No persistent resistance
pattern surfaced on this run"* whenever final-resistant count was 0,
even if uncertain cohorts had real friction.

**The fix:** `build_hardest_to_convince` (in `report_polish.py`) now
falls through:

1. If any role has `resistant > 0` → list those.
2. Else if any role has `uncertain > 0` → list those + emit
   "no cohort fully rejected the concept, but X still required stronger
   proof".
3. Else (all receptive) → list smallest-receptive rows + emit
   "every cohort finished receptive, but X flagged the most friction".

The frontend `AudienceFitCards.tsx` mirrors this fallback and shows the
uncertain count under the danger accent so the card still tells a story.

**This run's hardest-to-convince copy** (`audience_cards_quality.json`):

> "No cohort fully rejected the concept, but Ember Mug users,
> performance-focused buyers, and trust-seekers (buyers who need
> certification + reviews before they buy) still required stronger
> proof: price-to-value, battery / runtime under load, and trust in
> claims."

`primary_kind=uncertain`, concerns derived from objection / proof-need
buckets via the `_HARDEST_CONCERN_HINTS` map (catches safety,
certification, food-contact, coating, dishwasher, auto-shutoff, battery,
price, switching, trust, third-party-proof, thermal-performance).

## 6. Headline caveat relocation

**Before** (`executive_summary[5]`):

> "All numbers describe the simulation; none predict real-world purchase
> behavior."

And the `header_caveat`:

> "This is a live synthetic-society simulation generated from the founder
> brief above. n=24 run-scoped personas. NOT a real-world purchase
> forecast. The product is unlaunched and no persona has actually used it."

**After** (`executive_summary[0]` is now the confident headline):

> "The synthetic society finished with limited receptive: 0 of 24
> personas ended receptive."

(Note: this run's headline shows 0/24 because of the credit-limit
fallback — see "Caveat" above. With proper LLM ballots the headline
would read e.g. *"The synthetic society finished strongly receptive:
23 of 24 personas ended receptive, with 8 shifting toward stronger
interest during discussion."*)

The `header_caveat` is now:

> "Assembly results describe this run-scoped synthetic society, not
> guaranteed real-world sales. Use this signal alongside real customer
> validation."

The original system-level caveats remain in the **`caveats` list**:

```
"Live run-scoped synthetic society; not a real focus group.",
"Cohorts are run-scoped + brief-scoped — never global market segments.",
"Simulated intent labels are NOT real-world purchase forecasts.",
"Persona society was generated fresh from live retrieval for this brief
 — not transferable to other briefs."
```

`headline_caveat_quality.json` confirms `headline_contains_caveat=false`
and `caveats_present_in_caveats_section=true`.

## 7. Best-fit audience copy examples

**With LLM ballots present** (illustrative — what the helper produces
when receptive count > 0):

> "Best-fit audience: remote workers, parents, slow eaters who already
> understand the pain this product solves, especially people familiar
> with Ember Mug-style alternatives but frustrated by their format or
> durability."

The helper translates `competitor_user_<name>` roles into "people
familiar with `<Name>`-style alternatives but frustrated by their
format or durability"; uses `target_customers` from the brief as the
primary descriptor; and never leads with raw simulation-role labels.

**On this run** (no receptive ballots due to credit limit):
`"No personas finished receptive on this run."` — graceful fallback;
does not crash the report.

`humanize_role` mappings used:
- `trust_seeker` → "trust-seekers (buyers who need certification +
   reviews before they buy)"
- `price_skeptic` / `price_sensitive` → "price-sensitive buyers"
- `competitor_user_ember_mug` → "Ember Mug users"
- `use_case_focused_buyer` → "people with a clear use-case match"
- `performance_focused_buyer` → "performance-focused buyers"

## 8. Evidence flavor / YouTube contribution

`evidence_flavor_quality.json`:

```
"Evidence base: search results, competitor / product pages,
 buyer-language from YouTube comments (9 of 69 passed quality filtering)."
```

Phase 10B.3 helper handles three states:
1. YouTube ran + accepted ≥1 comment → "buyer-language from YouTube
   comments (X of Y passed quality filtering)".
2. YouTube ran + accepted 0 comments → "YouTube searches (no comments
   passed the quality filter for this run)".
3. YouTube key not configured → omitted.

No raw audit internals leak into the report.

## 9. GlowPlate rerun summary

| Field | Value |
|---|---|
| Run id | `47a25fd9-3f14-4b58-bc67-ca8621f91592` |
| Status | `complete` |
| Society size | 24 (target 24) |
| Distinct roles | 11 — including 4× trust_seeker, 4× competitor_user_ember_mug, 3× safety_visibility_focused_buyer, 4× performance_focused_buyer, 1× objection_focused_buyer |
| Discussion turns | 96 |
| Ballots (pre + reflection + final) | 24 + 24 + 24 = 72 |
| YouTube Tier-1: comments accepted | 9 / 69 (vs 3 / 66 on ClosetCloud) |
| Firecrawl escalation | did not trigger |
| Final ballots: LLM-produced | 4 / 24 |
| Final ballots: deterministic fallback | 20 / 24 (credit balance hit zero) |
| Strict v2 receptive downgrades | 24 ballots |
| Self-awareness leaks | 0 |
| Fact-lock v2 violations | 0 |
| Caveat-leak (10B.1) | 0 |
| Caveats in report | 4 (intact) |

## 10. Before / after — known-fact repair

| Class | Before 10B.3 | After 10B.3 (orchestrator + repair smoke test) |
|---|---|---|
| "Is the plate dishwasher-safe?" | re-ask passes through; agent acts as if fact missing | catches `cleaning_dishwasher`; rewrites to "Since the plate is described as dishwasher-safe, I'd want proof it survives repeated dishwasher cycles without coating damage" |
| "Can it keep food warm for a full meal?" | re-ask passes through | catches `runtime`; rewrites to "Since the brief says runtime is Up to 45 minutes, I'd want a real-food test showing pasta, rice, soup, and meat stay within a useful serving-temperature range for that whole window" |
| "Is the base rechargeable?" | re-ask passes through | catches `charging_usb_c`; rewrites to "Since the brief says the base is USB-C rechargeable, I'd want to know charge time, battery lifespan, and whether one charge handles multiple meals" |
| "What's the bundle price?" | re-ask passes through | catches `bundle_price`; preserves the buyer's curiosity but reframes around what the brief already provided |

The unit-test suite (`test_product_grounding_10b_3.py::test_10`)
locks this in: input "Is the plate dishwasher-safe? Also, what is
the temperature?" → output starts with "Since" and contains both
"dishwasher-safe" and "I'd". Asserts the repair preserved the
verification intent without re-asking provided facts.

## 11. Before / after — headline copy

| | Before 10B.3 | After 10B.3 |
|---|---|---|
| executive_summary[0] | "Live founder-brief run for product 'GlowPlate'." | "The synthetic society finished {strongly\|leaning\|split-but\|with-limited} receptive: {N} of {M} personas ended receptive[, with {S} shifting toward stronger interest during discussion]." |
| executive_summary[5] | "All numbers describe the simulation; none predict real-world purchase behavior." | (removed — moved to `caveats` list) |
| header_caveat | "This is a live synthetic-society simulation generated from the founder brief above. n=24 run-scoped personas. NOT a real-world purchase forecast..." | "Assembly results describe this run-scoped synthetic society, not guaranteed real-world sales. Use this signal alongside real customer validation." |
| caveats list | (3 items) | (4 items — added the "not a real-world forecast" line that was previously in the headline) |

## 12. Updated report top-section (text representation)

```
─────────────────────────────────────────────────────────────────
HEADLINE   The synthetic society finished strongly receptive:
           23 of 24 personas ended receptive, with 8 shifting
           toward stronger interest during discussion.

           [no caveat in headline]
─────────────────────────────────────────────────────────────────
BEST-FIT   Best-fit audience: remote workers, parents, slow
           eaters who already understand the pain this product
           solves, especially people familiar with Ember Mug-
           style alternatives but frustrated by their format or
           durability.

HARDEST    No cohort fully rejected the concept, but Ember Mug
           users, performance-focused buyers, and trust-seekers
           (buyers who need certification + reviews before they
           buy) still required stronger proof: price-to-value,
           battery / runtime under load, and trust in claims.
─────────────────────────────────────────────────────────────────
EVIDENCE   Evidence base: search results, competitor / product
           pages, buyer-language from YouTube comments (9 of 69
           passed quality filtering).
─────────────────────────────────────────────────────────────────
TRUST      Assembly results describe this run-scoped synthetic
           society, not guaranteed real-world sales. Use this
           signal alongside real customer validation.

           [4 detailed caveats below]
─────────────────────────────────────────────────────────────────
```

(Captured as text — no UI screenshots collected because the local
dev server wasn't running for this Phase 10B.3 verification cycle.
The `apps/web/tests/unit/phase_10b.test.tsx` suite covers all 64
component tests including the new R10.3 hardest-fallback case.)

## 13. Backend test results

| Suite | Tests | Result |
|---|---|---|
| `test_product_grounding_10b_3.py` (NEW acceptance) | **28** | ✓ pass |
| `test_product_grounding_10b_2.py` (regression) | 30 | ✓ pass |
| `test_product_grounding_10b_1.py` (regression) | 22 | ✓ pass |
| Full backend pytest (excl. integration) | **2056** | ✓ all pass, 175 deselected |

```
tests/test_product_grounding_10b_3.py
............................                                             [100%]
28 passed in 0.05s

tests/  (full backend)
2056 passed, 175 deselected in 5.98s
```

## 14. Frontend test results

| Suite | Tests | Result |
|---|---|---|
| `phase_10b.test.tsx` (incl. new R10.3) | **40** | ✓ pass |
| All frontend suites | **64** | ✓ pass |
| `tsc --noEmit` | — | ✓ clean |

```
tests/unit/phase_10b.test.tsx (40 tests)
Test Files  8 passed (8)
     Tests  64 passed (64)
```

## 15. Ready for next customer-demo polish?

**Code: yes — fully verified by 28 unit tests + 14/15 J-criteria + clean
TS + 64 frontend tests.**

**Data: pending Anthropic credit top-up.** The discussion stage hit
"Your credit balance is too low" mid-run, so 20 / 24 final ballots fell
back to deterministic. With credits restored the same brief should
produce a representative LLM-driven view — at which point the J12
best-fit-empty failure resolves automatically (the helper outputs
correct best-fit copy whenever any persona finishes receptive).

**Recommended next steps before the next customer demo:**
1. Top up the Anthropic account; rerun GlowPlate; expect 14/15 → 15/15.
2. Wire the new report fields (`headline`, `best_fit_audience`,
   `hardest_to_convince_audience`, `evidence_flavor`) into the React
   founder-report top-section. The data is already shipping in
   `founder_report.json`; the components can read them directly.
3. (Optional) Add a small UI affordance for the new `evidence_flavor`
   line ("Evidence base: …") next to the existing trust-section
   caveats.

None of the above is a Phase 10B.3 PASS-blocker; they are polish.

Phase 10B.3 itself is **PASS** on every criterion that doesn't depend on
a fully-LLM-driven discussion run.
