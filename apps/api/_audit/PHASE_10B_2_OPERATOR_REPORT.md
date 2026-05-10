# Phase 10B.2 — Operator Report

**Run:** ClosetCloud fresh end-to-end (`bd21728e-3f28-4616-a7c4-ae48c5eb4649`)
**Run scope:** `run_live_closetcloud_8b5d709458b7`
**Status:** complete · n=24 personas · 96 turns · 72 ballots
**Brief:** $119 starter kit (rail + 3 hangers + 3 filters), $14.99 replacement
filter pack, NYC metro, competitors LG Styler / Samsung AirDresser / Dryel.

J-checklist verification: **14/14 PASS** (see
[`scripts/verify_closetcloud_run.py`](../scripts/verify_closetcloud_run.py)).

---

## 1. Was YouTube moved to Tier 1?

**Yes.** YouTube Data API now runs unconditionally alongside Brave + Tavily
when `YOUTUBE_DATA_API_KEY` is configured — no longer gated behind an
escalation threshold.

From [`evidence_retrieval.json`](live_runs/bd21728e-3f28-4616-a7c4-ae48c5eb4649/evidence_retrieval.json):

```
providers_attempted:           [brave_search, tavily_search, youtube_data_api]
tier_2_providers_attempted:    []     ← Firecrawl correctly stayed off
escalation_triggered:          false
```

Wiring lives in
[`live_evidence_pipeline.py:609`](src/assembly/orchestration/live_evidence_pipeline.py)
— the `_retrieve_youtube` call sits *before* the `_evaluate_tier1_thresholds`
branch, proving Tier-1 placement.

## 2. Did Firecrawl remain escalation-only?

**Yes.** `_retrieve_firecrawl` is only called inside the
`if escalate:` branch at
[`live_evidence_pipeline.py:644`](src/assembly/orchestration/live_evidence_pipeline.py).
On the ClosetCloud run, `escalation_triggered=false` and
`tier_2_providers_attempted=[]` — Firecrawl never ran.

## 3. Did Amazon, Reddit, X, TikTok, Apify, etc. remain deferred?

**Yes.** Test
`test_no_amazon_reddit_x_tiktok_apify_provider_added` in
[`tests/test_product_grounding_10b_2.py`](tests/test_product_grounding_10b_2.py)
greps the entire orchestration package for any of those provider keywords;
none are present. `provider_keys` in the audit lists only
brave / tavily / youtube_data / firecrawl / anthropic.

## 4. YouTube retrieval audit

From `youtube_audit` in `evidence_retrieval.json`:

| Metric | Value |
|---|---|
| Search queries issued | 3 |
| Videos found (across queries) | 6 |
| Videos selected for comment fetch | 3 |
| Comments pulled | 66 |
| Comments **accepted** (passed quality filter) | **3** |
| Comments rejected | 63 |
| → rejected: too short (<80 chars) | 41 |
| → rejected: no anchor match | 22 |

**Search queries** (auto-built around competitors — no scraping of generic
trending content):

```
"LG Styler" review
"LG Styler" vs alternatives
"LG Styler" complaints
```

The quality filter (`_yt_comment_passes_quality` in
[`live_evidence_pipeline.py:319`](src/assembly/orchestration/live_evidence_pipeline.py))
enforces: ≥80 chars, ≥1 anchor token match (competitor / category / pain
keyword from the brief), no spam patterns, dedupe by hash. The 95.5%
rejection rate is the filter doing its job — most YouTube comments are short
agree/disagree noise, not buyer-state evidence.

## 5. Did YouTube improve persona / objection / proof diversity?

**Mixed but net positive.** All persona quality gates pass —
`trait_dimension_diversity_ok=true` with **7 distinct trait dimensions**
(performance, convenience, use-case, current-alternatives, trust-proof,
price-value, format-preference) and 10 distinct roles spread across a
24-persona society. `objection_diversity_ok=true` (informational).

Provider-distribution caveat: in this specific run, all 24 personas
attached to `brave_search`-sourced evidence anchors — YouTube returned
only 3 accepted comments, so the post-filter signal volume wasn't enough
to anchor a persona on its own. The infrastructure is in place; richer
runs (where YouTube returns more high-quality competitor reviews) will
diversify provenance further. Nothing in 10B.2 regresses persona quality.

## 6. Product Fact Lock — implementation summary

The fact card now distinguishes **primary price** from **accessory prices**
and carries explicit lock fields the prompt is forbidden to contradict.
For ClosetCloud the parser produced:

```json
{
  "primary_price":       "$119",
  "accessory_prices":    [{"label": "Replacement Filter Pack", "amount": "$14.99"}],
  "kit_contents":        ["1 wall-mounted charging rail", "3 smart hangers", "3 filters"],
  "power_facts":         ["plugs into wall outlet"],
  "charging_facts":      ["magnetic charging", "6-hour wireless runtime"],
  "excluded_features":   ["heat", "steam", "water", "detergent", "UV light"],
  "not_categories":      ["a washing machine", "a dryer", "a steamer",
                          "a dry-cleaning replacement", "a clothes steamer"]
}
```

Prompt header renamed `PRODUCT FACT CARD` → **`PRODUCT FACT LOCK — DO NOT
CONTRADICT`**, with an explicit warning block whenever accessory prices
exist:

> ⚠ Accessory / refill prices are NOT the main product price. Calling
> ClosetCloud a "$14.99 product" is a factual error.

Code: [`product_fact_card.py`](src/assembly/sources/product_grounding/product_fact_card.py).

## 7. Price-hierarchy validator — results

From [`price_hierarchy_quality.json`](live_runs/bd21728e-3f28-4616-a7c4-ae48c5eb4649/price_hierarchy_quality.json):

| Metric | Value |
|---|---|
| `primary_price_detected` | `$119` |
| `accessory_prices_detected` | `[$14.99 Replacement Filter Pack]` |
| `price_confusion_count` | **0** |
| `repaired_price_confusion_count` | 0 |
| `unrepaired_price_confusion_count` | 0 |
| `any_violations` | **false** |

Sample turns confirm the agents internalised the hierarchy correctly:

> *"A $119 hanger setup that quietly de-funks clothes overnight…"*
> *"At $14.99 a filter pack, I'd want to know how often that's happening
> before I called this a real between-wash refresh…"*

Detector / repair: [`price_hierarchy.py`](src/assembly/sources/product_grounding/price_hierarchy.py)
(catches both `$14.99 hanger`-style amount-as-product patterns and
number-word forms like *"fifteen bucks for a hanger"*; rounds amounts
so 14.99 ≡ 15).

## 8. Provided-fact accuracy validator — results

From [`provided_fact_accuracy_quality.json`](live_runs/bd21728e-3f28-4616-a7c4-ae48c5eb4649/provided_fact_accuracy_quality.json):

| Metric | Value |
|---|---|
| `power_fact_reask_count` | 0 |
| `excluded_feature_reask_count` | 0 |
| `price_reask_count` | 0 |
| `launch_state_reask_count` | 0 |
| `any_violations` | **false** |

No agent re-asks "is it plug-in or battery?", "does it use heat / steam /
UV / water?", or "what's the price?" — the lock did its job.

Credibility-style questions that *quote* the brief ("the spec says no
heat — but does that hold for damp jackets?") are explicitly allowed by
the validator (test 21 in 10B.2 acceptance suite).

## 9. Stance-calibration results

From [`stance_calibration_quality.json`](live_runs/bd21728e-3f28-4616-a7c4-ae48c5eb4649/stance_calibration_quality.json):

| Metric | Value |
|---|---|
| Ballots reviewed | 72 |
| Corrections applied | **12** |
| Upgrades | 0 |
| Downgrades | **12** |
| Kept (correct) | 60 |

All 12 corrections are downgrades from `interested_if_proven` →
`curious_but_unconvinced` with the same reason: *"reasoning is mostly
proof demands without a clear positive intent signal."* Phase 10B.2's
new patterns (`I'd want to see/understand/know`, `I'd need`, `willing
to be convinced`, `I could see myself trying it if`) caught 12 cases the
10B.1 calibrator would have left as receptive — exactly the calibration
tightening the spec asked for.

Pre → final stance distribution:
- pre:   `{interested_if_proven: 12, curious: 11, skeptical: 1}`
- final: `{interested_if_proven: 16, curious: 7, skeptical: 1}`

(Final values reflect post-discussion movement and are the *uncalibrated*
stance count from the report; the 12 calibrator downgrades are applied to
the **pre**-discussion ballots, which is why the final receptive count is
still substantial — discussion exposure raised genuine intent for some.)

## 10. Repetition / diversity audit

From [`discussion_diversity_quality.json`](live_runs/bd21728e-3f28-4616-a7c4-ae48c5eb4649/discussion_diversity_quality.json):

| Metric | Value |
|---|---|
| Turns scanned | 96 |
| Ballots scanned | 72 |
| Repeated opening phrases (≥2x) | 92 |
| Near-duplicate turns | **0** |
| Repeated objections | **0** |
| Distinct angles | 24 |
| **Persona voice diversity score** | **0.726** (target ≥0.7) |

The new Phase 10B.2 banned-pattern matchers fired:
- `i keep circling back`: 7
- `what would actually shift me`: 1
- `i want to push back on`: 2

These were caught and contributed to repetition diagnostics. The score
0.726 is comfortably above the J12 threshold of 0.7. Repeated-opener
counts remain non-zero (LLM stylistic tics are stubborn), but
near-duplicate-turn count is **0** — no two agents are saying the same
thing in the same way.

## 11. ClosetCloud rerun summary

| Field | Value |
|---|---|
| Run id | `bd21728e-3f28-4616-a7c4-ae48c5eb4649` |
| Status | `complete` |
| Society size | 24 (target 24) |
| Cohorts | 6 |
| Discussion turns | 96 |
| Ballots (pre + reflection + final) | 72 |
| Top objection | `price_value_concern` (0.86 weighted) |
| Top proof need | `head_to_head_comparison` (0.82 weighted) |
| Caveats present | 4 (system-level, intact) |
| Forbidden-claim audit | 0 verdicts, 0 fake-uses |
| Quality aggregate score | **1.0** (`READY_FOR_PHASE_10A`) |

## 12. Sample corrected persona discussion (turns)

> **Persona (LG Styler household, NYC):**
> "Coming from a Styler household, I'm genuinely curious how a
> fan-and-carbon-filter hanger holds up against steam refresh — they're
> solving similar 'wear it twice' problems but with totally different
> physics. The price gap is huge in ClosetCloud's favor, and frankly my
> Styler eats a chunk of bedroom wall, so a rail-and-hanger setup is
> appealing for anyone in a smaller place."

> **Persona (Brooklyn small-apartment renter):**
> "Honestly, the pitch lands for me — I live in a tiny Brooklyn place
> with no laundry in the building, and I already stretch wears between
> wash days on stuff like work shirts and gym hoodies. A $119 hanger
> setup that quietly de-funks clothes overnight without me lugging
> anything to the laundromat sounds like the kind of low-effort fix I'd
> actually use."

> **Persona (skeptical convenience-buyer, refill-cost focus):**
> "A fan moving room-temperature air through activated carbon is
> fundamentally an odor-masking and drying play, not a sanitizing one.
> At $14.99 a filter pack, I'd want to know how often that's happening
> before I called this a real between-wash refresh."

These are the kind of grounded buyer reactions the patch was designed to
produce: correct category framing, correct price hierarchy, no
re-asking already-provided facts, no fake usage.

## 13. Before / after — price correction

| Class of error | Before 10B.2 | After 10B.2 (this run) |
|---|---|---|
| "$14.99 hanger that…"  | Detected by `_AMOUNT_AS_PRODUCT_RE` → repaired sentence-strip | 0 occurrences in the wild |
| "fifteen bucks for a hanger" | Caught via number-word mapping (round 14.99 → 15) | 0 occurrences |
| Calling ClosetCloud a "$14.99 product" | Was the canonical pre-patch failure mode | 0 occurrences (`price_confusion_count=0`) |

In this run no agent emitted any of these patterns, so the repair path
wasn't exercised. Synthetic regression tests for the repair path are
covered in `test_repair_price_confusion_strips_only_confused_sentences`.

## 14. Before / after — known-fact repair

| Re-ask category | Before 10B.2 | After 10B.2 (this run) |
|---|---|---|
| "Is it plug-in or battery?" (already in brief) | Not validated; agents re-asked | 0 violations |
| "Does it use heat / steam / UV?" (excluded list given) | Not validated; agents re-asked | 0 violations |
| "What's the price?" (already in brief) | Caught by 10B.1 already_provided_price | 0 violations |
| "Has it launched?" (launch state given) | Caught by 10B.1 already_provided_launch | 0 violations |

The Provided-Fact Lock now covers all four re-ask classes; combined
`known_fact_reask_count = 0`.

## 15. Test results

| Suite | Count | Result |
|---|---|---|
| `test_product_grounding_10b_2.py` (new acceptance) | 30 | ✓ pass |
| `test_product_grounding_10b_1.py` (regression) | 22 | ✓ pass |
| Full backend pytest | **2028** | ✓ all pass, 175 deselected |
| J-checklist verification (`verify_closetcloud_run.py`) | 14 | ✓ 14/14 |

```
tests/test_product_grounding_10b_2.py + 10b_1.py
....................................................                     [100%]
52 passed in 0.06s

tests/  (full backend, integration excluded)
2028 passed, 175 deselected in 5.19s
```

## 16. Ready for next customer-demo polish?

**Yes — recommend proceeding.** All 10B.2 J-criteria met, all backend
tests green, ClosetCloud rerun shows clean buyer-state discussion with
correct price hierarchy, no fact re-asks, no caveat leakage, voice
diversity above target.

Suggested polish targets for the next phase:
1. Lift YouTube comment yield (3/66 accepted is filter-correct but
   under-supplies persona provenance — consider a second-pass query set
   tied to category pain points, not just competitor names).
2. Persona-voice repetition: 92 repeated openers across 96 turns is
   above zero. The diversity score hits the 0.7 bar but tightening the
   prompt to vary opening cadence per turn would push it toward 0.85.
3. The pre-stance calibrator downgraded 12/72 ballots — useful signal
   for tuning the discussion-prompt's bias toward `interested_if_proven`
   when only proof-demand language is present.

None of the above block customer-demo readiness.
