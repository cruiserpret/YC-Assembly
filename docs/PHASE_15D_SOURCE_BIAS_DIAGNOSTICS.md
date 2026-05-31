# Phase 15D0 — Source-Bias & Category-Prior Diagnostics

> **⚠️ Phase 15D0 does not change forecasts. It measures repeated error
> patterns only.**
> No calibration is applied, no prior is applied, no live output changes, no
> accuracy is claimed. This is the diagnostic groundwork that a *future* Phase
> 15E calibrated forecast would be built on — and only after the data
> discipline below is met.

Part of the Phase 15 roadmap
([scope](PHASE_15_MARKET_EVIDENCE_AND_CALIBRATION_SCOPE.md),
[ledger](PHASE_15B_VALIDATION_LEDGER.md),
[action signals](PHASE_15C_ACTION_SIGNAL_WEIGHTING.md)).

Code: `apps/api/src/assembly/market_calibration/` —
`source_profiles.py`, `category_priors.py`, `calibration_diagnostics.py`.
CLI: `apps/api/scripts/phase_15d_calibration_diagnostics.py`.

> **Architecture note.** Phase 15C's action-signal *representation* layer
> (`action_signals.py`, `signal_weights.py`) is deliberately **leakage-free** —
> it never imports the ledger or sees observed outcomes. The Phase 15D
> *diagnostic* modules **must** read observed outcomes (that is how they measure
> error), so the leakage guard is scoped to the representation files; the
> diagnostic modules are exempt and provably emit **no forecast**.

## What source-bias diagnostics are

For each ingestion `source_type`, `estimate_source_profiles` compares Assembly's
locked predictions against observed outcomes and reports the **repeated
per-source error pattern**: average MAE/TVD/max-bucket-error, the **signed**
per-bucket bias (predicted − observed; + = over-predicted, − = under-predicted),
which buckets are over/under-predicted, and a confidence level. On the current
6-case seed this already surfaces a clear, consistent pattern:

| Source | n | Repeated bias (diagnostic only) |
|---|---:|---|
| **Hacker News** | 3 | **over-predicts receptive (~+43pp)**, under-predicts skeptical + proof-needed |
| **Product Hunt** | 2 | **over-predicts skeptical (~+60pp)**, under-predicts buyer + proof-needed |
| **Kickstarter** | 1 | **under-predicts buyer (~−30pp)** — backers are buyers the comment thread hid (the Tiiny miss) |

These are *measurements*, not corrections. Nothing is applied.

## What category-prior diagnostics are

For each `product_category`, `estimate_category_profiles` reports the average
**observed** vs **predicted** bucket distribution, the signed per-bucket error,
and average MAE — the diagnostic precursor to a learned category prior. No prior
is applied and no generalizable-prior claim is made for thin categories.

## Why HN, Product Hunt, Kickstarter, GitHub, Reddit, … need separate profiles

Each channel draws a structurally different audience, so each distorts the
observed reaction in a characteristic direction: Hacker News skews
technical/skeptical and proof-demanding; Product Hunt skews enthusiast and
novelty-seeking; Kickstarter skews early-adopter, and **backing is a purchase**
that public caution masks; GitHub stars/forks mean adoption for dev tools but
little for consumer apps; Reddit varies by subreddit. A single global correction
would be wrong for all of them — the bias must be modeled **per source** (and
per category), which is exactly what these profiles isolate.

## Why the current profiles are diagnostic only

- **Only 6 cases** (1–3 per source) — far too few to generalize; every profile
  is `weak`/`insufficient` confidence.
- **0 holdout cases** — there is nothing unseen to validate a correction
  against, so the report is explicitly `validated: false` and `not validated`.
- **Most observed outcomes are Tier-3/comment-derived** (independent-voice
  analysis of threads), not Tier-1 revealed action — so even the "ground truth"
  carries the biases we are trying to correct.

The report makes all three explicit as warnings, plus: *"Do not apply these
profiles to live forecasts yet."*

## Why 0 holdout cases prevents accuracy claims

Calibration estimated and tested on the same cases is indistinguishable from
overfitting. A correction can only be **trusted** if it improves predictions on
cases it never saw. With 0 holdout cases, no held-out test is possible, so **no
calibrated-accuracy claim is permitted** — and 15D0 changes no forecast, so it
cannot manufacture one.

## Why Tier-1 action outcomes matter

The seed's observed proportions come from comment analysis (Tier 3), which is
biased for *how many* (see [15C](PHASE_15C_ACTION_SIGNAL_WEIGHTING.md)). To
calibrate real proportions we need **Tier-1 revealed-action ground truth** —
purchases, backers, paid signups, trial conversions, installs/downloads,
retention/churn. The Kickstarter/Tiiny miss (0% predicted buyers vs ~30% real
backers) is the clearest example: the right ground truth is the backer count,
not the comment sentiment.

## How this feeds Phase 15E

Phase 15E (calibrated forecast prototype, flag-gated, off by default) would:
1. **fit** source/category corrections on the ledger's `used_for_training`
   split (the machinery here),
2. **apply** them behind a flag to produce a *calibrated* distribution
   alongside (never replacing) the current forecast,
3. and **score** the calibrated output against the uncalibrated baseline on the
   **holdout** split (Phase 15F). Only a held-out win authorizes surfacing
   calibrated numbers to founders.

15D0 builds step 1's *measurement*; it does not perform steps 2–3.

## Anti-overfit rules (enforced here + by tests)

- Fit on `used_for_training` cases **only**; **holdout cases are never used to
  fit** (a test proves a holdout case cannot leak into a profile).
- Scored cases only; no product-name logic anywhere.
- Observed outcomes are used **only to measure** error — never as a model input
  and never applied to a forecast (a test proves the report emits no
  market-bucket distribution).
- Small N and missing holdout are surfaced as warnings; confidence is **capped
  at `weak`** unless the observed ground truth includes real revealed action.
- No correction may be applied until the minimum data requirements (below) are
  met and a holdout win is demonstrated.

## Minimum data requirements before applying corrections

1. **≥ 20 validation cases**, diverse across sources, categories, and price
   models (not 20 dev tools).
2. **A real holdout split** — new cases default to `used_for_holdout=true`; a
   correction is fit on training and validated on holdout.
3. **Tier-1 action outcomes** captured per case where possible (the ledger's
   `denominator_type` records this), so the ground truth is revealed action,
   not comment sentiment.
4. **A repeated pattern across multiple cases** — no per-source/per-category
   adjustment unless the bias recurs (no single-case fit).
5. **A held-out win** vs the uncalibrated baseline (Phase 15F) before any
   calibrated number is shown to a founder.

---

*Phase 15D0 is diagnostics only. It reveals repeated source/category error
patterns to guide future calibration while protecting against overfitting — and
it changes no forecast.*
