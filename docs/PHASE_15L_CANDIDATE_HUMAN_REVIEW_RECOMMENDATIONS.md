# Phase 15L-A — Candidate Human-Review Recommendations

**Review preparation only. No candidate approved, no case ingested, no four-bucket
mapping invented, no calibration, no forecast change. Official ledger unchanged
(6 training / 0 holdout / 0 pending). Phase 15E remains BLOCKED.**

Prepared by a 4-agent read-only workflow (source re-verification, four-bucket
mapping defensibility, evidence-tier/action audit, anti-overfit suitability),
centralized here. All recommendations are **for the human reviewer** — the final
`reviewer_checklist`, the four-bucket mapping, and the approve/ingest decision
remain the human's.

## The binding finding (read this first)

Every one of the 8 candidates provides only a **buyer/action-positive numerator**
(backers, downloads, forks, units) over a **self-selected denominator**. The
**receptive / uncertain-proof-needed / skeptical-resistant** proportions of the
full addressable market are **not externally observable** from any source. So:

- **No candidate has a defensible four-bucket *observed* mapping** derivable from
  its source. `claimed_outcome_proportions` is correctly **null** on all 8, and
  proposing the three non-buyer buckets would be fabrication.
- Therefore the **net status for all 8 is `keep_needs_review`** until a human
  either (a) supplies an **explicitly-labeled, assumption-based** four-bucket
  mapping (documenting the assumptions in `reviewer_notes`), or (b) decides to
  use them as **action-signal evidence only** (note: the factory's training/holdout
  gates require `claimed_outcome_proportions`, so without a mapping they cannot be
  promoted to training).
- These are nonetheless **the first real Tier-1 action-outcome cases** — the seed
  has 0/6 action signals — so their evidence value is high even though their
  full-distribution value is gated on human judgment.

## Summary table

| metric | value |
|---|---|
| candidates reviewed | **8** |
| sources re-verified | 8/8 (6 clean verbatim, 2 immaterial drift) |
| net status (mapping-gated) | **8 keep_needs_review** · 0 ready-to-map · 0 reject |
| training-*value* if a mapping is supplied | **5 high/med-value** (exploding_kittens, coolest_cooler, vox_machina, clubhouse, humane) · **3 need an extra decision** (pebble_time + pebble_original → de-dup; automatic1111 → time-box) |
| recommended rejection | **0** (all carry a verified action signal) |
| evidence tier | **8 Tier-1** (automatic1111 also Tier-2 star); **4 "weaker than it looks"** (clubhouse, automatic1111, coolest_cooler, humane) |
| weak / ambiguous numerators | clubhouse + automatic1111 (free actions), humane (press estimate) |
| **concentration warning** | **5/8 Kickstarter · 5/8 crowdfunding · 4/8 consumer hardware (wearables ×3) · 2 Pebbles = same company (non-independent) · 6/8 single-Wikipedia-source · 5/8 successes** |
| can be clean holdout | **0/8** (all retrospective, known outcome) |
| Phase 15E readiness impact if all mapped+ingested as training | ledger 6 → **14** (still < 20) · clean holdout still **0** · Tier-1/2 outcome cases 0 → **8** (that requirement met). **Still BLOCKED** on case count + clean holdout. |

**Anti-overfit cap (Agent 4):** if admitting, cap Kickstarter at ≤2–3, admit
**at most one** Pebble entry, and over-weight the non-crowdfunding / divergence
cases (clubhouse, humane, coolest_cooler, automatic1111) so the training
distribution does not collapse onto crowdfunding dynamics.

## Candidate-by-candidate

Legend — **Tier**: Assembly evidence tier · **Map**: four-bucket defensibility
(partial/no) · **Value**: training value *if a human supplies a labeled mapping*
· **Net**: actionable status now.

### 1. pebble_time_kickstarter_2015 — Pebble Time
- **Category:** consumer_hardware_wearable · **Source:** https://en.wikipedia.org/wiki/Pebble_(watch)
- **Verified:** 78,471 backers / $20,338,986, closed 2015-03-27 (verbatim ✓). Company wound down → Fitbit Dec 2016.
- **Tier:** 1 (paid preorder, not weaker-than-looks) · **Action signal:** `kickstarter_pledge` 78,471 (paid).
- **Four-bucket mapping:** partial → **null** (confidence high). Only the buyer anchor is observable; non-buyer buckets unobservable.
- **Uncertainty flags:** self-selection, captive-fanbase, repeat-backer correlation with the 2012 campaign, single-source.
- **Value:** LOW (near-duplicate of the 2012 Pebble entry) · **Net: keep_needs_review.**
- **Human decision needed:** de-dup vs `pebble_original_kickstarter_2012` — admit **at most one** Pebble (or treat the pair as one down-weighted observation). Then decide a labeled mapping.

### 2. pebble_original_kickstarter_2012 — Pebble (original)
- **Category:** consumer_hardware_wearable · **Source:** https://en.wikipedia.org/wiki/Pebble_(watch)
- **Verified:** 68,929 backers / $10,266,845, closed 2012-05-18 (~100× the $100k goal) (verbatim ✓).
- **Tier:** 1 (paid preorder) · **Action signal:** `kickstarter_pledge` 68,929 (paid).
- **Four-bucket mapping:** partial → **null** (high). Extreme self-selection (100× goal).
- **Uncertainty flags:** self-selection, extreme-self-selection, captive-fanbase, single-source, redundant with the 2015 entry.
- **Value:** LOW (correlated pair) · **Net: keep_needs_review.**
- **Human decision needed:** same de-dup decision as #1. (Agent 4 marginally prefers keeping this one — cleaner record-success outcome — if only one Pebble is admitted.)

### 3. exploding_kittens_kickstarter_2015 — Exploding Kittens
- **Category:** tabletop_card_game · **Source:** https://en.wikipedia.org/wiki/Exploding_Kittens
- **Verified:** 219,382 backers / $8,782,571, completed 2015-02-19, **delivered to all backers by Sep 2015** (verbatim ✓).
- **Tier:** 1 (paid preorder, fulfilled — not weaker-than-looks) · **Action signal:** `kickstarter_pledge` 219,382 (paid).
- **Four-bucket mapping:** partial → **null** (high). Clean fulfilled positive; creator-audience (The Oatmeal) bias.
- **Value:** MEDIUM (breaks the hardware concentration; clean fulfilled exemplar) · **Net: keep_needs_review** (mapping gate).
- **Human decision needed:** supply a labeled mapping recording the creator-audience inflation; then promote to training.

### 4. coolest_cooler_kickstarter_2014 — Coolest Cooler
- **Category:** consumer_hardware_appliance · **Source:** https://en.wikipedia.org/wiki/Coolest_Cooler
- **Verified:** 62,642 backers / $13,285,226 (Aug 2014); **>20,000 (~32%) never received the product**; shut down Dec 2019 (verbatim ✓).
- **Tier:** 1 + a negative `churn` signal (**weaker-than-looks: true** — realized buyers below pledged).
- **Four-bucket mapping:** partial → **null** (high). The delivered-vs-unfulfilled split is a *within-buyer fulfillment* split, **not** the non-buyer receptive/uncertain/skeptical buckets — do not conflate dissatisfied buyers with market skeptics.
- **Value:** HIGH (divergence case: strong action → fulfillment failure) · **Net: keep_needs_review.**
- **Human decision needed:** decide how to represent the >32% non-fulfilled cohort without inventing the non-buyer split; churn count is medium-confidence.

### 5. vox_machina_kickstarter_2019 — Critical Role: Vox Machina
- **Category:** creator_media_animation · **Source:** https://en.wikipedia.org/wiki/The_Legend_of_Vox_Machina
- **Verified:** 88,887 backers / $11,385,449, closed 2019-04-19; Amazon series pickup Nov 2019 (verbatim ✓).
- **Tier:** 1 (paid preorder, not weaker-than-looks) · **Action signal:** `kickstarter_pledge` 88,887 (paid).
- **Four-bucket mapping:** partial → **null** (high). Captive Critical Role fanbase.
- **Value:** MEDIUM (media-category diversity; clean positive downstream) · **Net: keep_needs_review.**
- **Human decision needed:** use only the **final** $11,385,449 (not the mid-campaign $5.79M Variety snapshot); record fanbase bias; supply mapping.

### 6. clubhouse_app_launch_2021 — Clubhouse
- **Category:** consumer_social_app · **Source:** https://en.wikipedia.org/wiki/Clubhouse_(app)
- **Verified:** ~3.5M downloads (Feb 1) → **8.1M by Feb 15 2021**; >10M WAU then **~21% decline** in three weeks (verbatim ✓; counts are third-party estimates).
- **Tier:** 1 by taxonomy (download) but **FREE install — weaker-than-looks: true**; consider Tier-2 framing.
- **Four-bucket mapping:** **no** → **null** (high). Free installs are not buyers; the count is an estimate; no buyer numerator at all.
- **Value:** HIGH (breaks crowdfunding monoculture; hype-spike→decline failure mode) · **Net: keep_needs_review.**
- **Human decision needed:** decide whether to treat free installs as a (weak) buyer proxy or as Tier-2; record the estimate-quality and retention-collapse caveats.

### 7. automatic1111_sdwebui_oss_2022 — AUTOMATIC1111 / SD-webui
- **Category:** open_source_ai_tool · **Source:** https://api.github.com/repos/AUTOMATIC1111/stable-diffusion-webui
- **Verified:** 30,369 forks + 163,371 stars, created 2022-08-22 (live API; star drifted 163,371→**163,372**, +1, a disclosed snapshot — immaterial).
- **Tier:** 1 (fork) + 2 (star), correctly typed; **FREE, cumulative, no denominator — weaker-than-looks: true.**
- **Four-bucket mapping:** **no** → **null** (high). Cumulative free counts with no time-box and no denominator — even a buyer-share proportion can't be computed.
- **Value:** HIGH diversity (only non-consumer, non-crowdfunding action type) · **Net: keep_needs_review.**
- **Human decision needed:** decide whether to **time-box** the cumulative snapshot (the candidate notes "or pending if a time-boxed snapshot is preferred") and confirm fork=action mapping before any promotion.

### 8. humane_ai_pin_launch_2024 — Humane AI Pin
- **Category:** ai_hardware_wearable · **Source:** https://en.wikipedia.org/wiki/Humane_Inc.
- **Verified:** ~10,000 units shipped by Aug 2024 (vs 100,000 hoped); **returns outpaced sales** May–Aug 2024; HP acquired Humane (~$116M); discontinued (verbatim ✓; figures are press estimates).
- **Tier:** 1 (real $699 paid purchase — the strongest *paid* signal) but **weaker-than-looks: true** (net buyers below ~10k gross).
- **Four-bucket mapping:** partial → **null** (confidence **medium** — press estimate). Strongest *directional* market-rejection case, but the implied skeptical share must be reasoned, not assigned a precise proportion.
- **Value:** HIGH (market-rejection, non-crowdfunding, pulls against the success-heavy set) · **Net: keep_needs_review.**
- **Human decision needed:** record the press-estimate caveat; decide how to represent the rejection direction without inventing a precise skeptical proportion.

## Four-bucket mapping risks (which have a defensible mapping, which do not)

- **Defensible full four-bucket observed mapping: NONE (0/8).** Every case is a
  buyer-numerator-only observation over a self-selected denominator.
- **"Partial" (a solid buyer anchor, non-buyer buckets unobservable): 6** —
  pebble_time, pebble_original, exploding_kittens, coolest_cooler, vox_machina,
  humane.
- **"No" (even the buyer anchor is soft): 2** — clubhouse (free-install
  estimates), automatic1111 (free cumulative, no denominator).
- **Do not conflate** the within-buyer fulfillment/return splits (Coolest Cooler,
  Humane) with the non-buyer skeptical bucket — they are dissatisfied buyers, not
  market skeptics.

## Exact commands to run later (only after the human review)

For any candidate the human chooses to admit, **after** the human (1) completes
`reviewer_checklist`, (2) sets `evidence_tier`, and (3) adds an
**explicitly-labeled, assumption-based** `claimed_outcome_proportions` to the
candidate file:

```bash
cd apps/api
python scripts/phase_15j_candidate_factory.py approve --id <candidate_id> --target training --dry-run
python scripts/phase_15j_candidate_factory.py approve --id <candidate_id> --target training
python scripts/phase_15j_candidate_factory.py ingest  --id <candidate_id> --dry-run
python scripts/phase_15j_candidate_factory.py ingest  --id <candidate_id>
# or reject:
python scripts/phase_15j_candidate_factory.py reject  --id <candidate_id> --reason "<why>"
```

Holdout is **not** an option for any of these (retrospective known outcome → the
anti-leakage gate blocks it). A clean holdout must come from the **prospective**
path: lock an Assembly prediction *before* the outcome (Phase 14C + 15I).

## Why Phase 15E is still blocked

Even if the human maps and ingests all 8 as training, the ledger reaches **14
cases (< 20)**, **0 clean holdout**, and **8 Tier-1/2 action-outcome cases**.
The Tier-1/2 requirement would be met, but Phase 15E stays **blocked** on:
**≥20 diverse reviewed cases** (need ~6+ more, ideally non-crowdfunding) and
**≥1 clean holdout** (only obtainable prospectively via 14C+15I). No calibration
runs until all three thresholds are met.
