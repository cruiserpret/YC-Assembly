# Phase 15G — Validation Dataset Expansion

> **Data infrastructure only.** Phase 15G adds the *structure and tooling* to
> grow Assembly's validation ledger safely. It applies no calibration, changes
> no live forecast, claims no accuracy, and **adds no invented data** — the
> ledger still holds the same 6 real seed cases. Its job is to make future
> calibration (15E/15F) honest.

Part of the Phase 15 roadmap
([scope](PHASE_15_MARKET_EVIDENCE_AND_CALIBRATION_SCOPE.md),
[ledger](PHASE_15B_VALIDATION_LEDGER.md),
[action signals](PHASE_15C_ACTION_SIGNAL_WEIGHTING.md),
[diagnostics](PHASE_15D_SOURCE_BIAS_DIAGNOSTICS.md)).

## Why Phase 15E is blocked by data

A calibrated forecast that is fit and tested on the same handful of cases is
overfitting, not calibration. Right now the ledger has **6 cases, all
training, 0 holdout, mostly Tier-3/comment-derived outcomes, and no populated
action_signals**. Fitting source/category corrections on that and "validating"
on the same cases would manufacture a false accuracy claim. **15E does not
start until the data discipline below exists** — that is what 15G builds.

- **Why 6 training cases are insufficient:** 1–3 cases per source can't
  separate a real, repeatable bias from noise; every Phase 15D profile is
  `weak`/`insufficient` for exactly this reason.
- **Why holdout cases are required:** a correction is only trustworthy if it
  improves predictions on cases it never saw. With 0 holdout cases there is
  nothing to validate against.
- **Why Tier-1 action outcomes matter:** the seed's observed proportions are
  comment-analysis (Tier 3), which is biased for *how many*. Calibrating real
  proportions needs revealed-action ground truth — purchases, backers, paid
  signups, installs, retention (see [15C](PHASE_15C_ACTION_SIGNAL_WEIGHTING.md)).

## Structure chosen: split files + manifest (Option A)

The ledger is now a set of files merged by a manifest, instead of one growing
file:

```
apps/api/validation_cases/
  manifest.json          # lists the files the loader merges (deduped by case_id)
  seed_cases.json        # the original 6 — UNCHANGED from Phase 15B
  holdout_cases.json     # blind, unseen cases (starts EMPTY)
  pending_cases.json     # locked predictions, outcome not yet observed (EMPTY)
  templates/validation_case_payload.example.json
```

Why split files (not one big file): physically separating holdout from training
makes accidental leakage harder (you don't casually edit the holdout file), and
pending cases can exist without observed outcomes. **Backward-compatible:**
`load_cases()` still loads `seed_cases.json` exactly as before; the new
`load_all_cases()` merges everything in the manifest and **falls back to
`seed_cases.json` if no manifest exists**.

## How to add a case

1. Copy `templates/validation_case_payload.example.json` → fill `example_case`
   with **real, externally-reviewed** data (do **not** invent outcomes), save
   it as its own JSON file.
2. Run:
   ```
   cd apps/api
   python scripts/phase_15g_add_validation_case.py path/to/your_case.json
   ```
   It validates the schema, runs the lock + leakage checks, and appends to the
   right split file (or refuses with a reason). Use `--to <file>` to override
   the target; `--allow-partial` to store a flagged case.
3. Check the dataset:
   ```
   python scripts/phase_15g_validation_dataset_summary.py
   ```

### pending vs scored
- **pending** — the prediction is locked but the outcome isn't known yet; omit
  `observed`. Lives in `pending_cases.json`.
- **scored** — both `predicted` and `observed` present (the schema enforces
  this); becomes eligible for diagnostics/metrics.

### training vs holdout
- **New cases default to `used_for_holdout=true`** (blind). They go in
  `holdout_cases.json` and must never be used to fit a correction.
- A case is **training** only if it already informed a model/dev decision (the
  6 seed cases). The schema forbids a case being **both** training and holdout.

### populating action_signals
Add the real revealed/semi-revealed actions to each case's `action_signals`
list using the Phase 15C taxonomy — e.g. `kickstarter_pledge`/`purchase`/
`github_fork` (Tier 1), `github_star`/`product_hunt_upvote`/`waitlist_signup`
(Tier 2) — with `count`/`denominator`/`direction`. Set the observed
`denominator_type` to the real action grade (e.g. `backers`) where action data
exists. This is what lifts the diagnostics' tier coverage and confidence off
the floor.

## How leakage checks work

`scripts/phase_15g_add_validation_case.py` runs two deterministic checks before
appending (and they're available as `validate_prediction_lock` /
`validate_no_outcome_leakage`):
- **Lock present:** scored/holdout cases must carry
  `prediction_lock.locked_prediction_created_at`.
- **No peeking:** if `observed.observed_at` is set, it must **not be earlier**
  than the locked prediction date — a prediction must be locked *before* the
  outcome is seen.
- **Explicit risk:** `leakage_risk` must be set (not `unknown`) for
  scored/holdout cases; a `high` case may be stored but is **excluded from
  clean holdout** by default (`is_clean_holdout()` returns False).

## How this prevents overfitting

- New cases default to **holdout** (unseen) — corrections fit on training,
  scored on holdout.
- The schema forbids train/holdout overlap; the summary flags any overlap.
- Locked-prediction + no-peeking checks stop outcome leakage at ingest time.
- The summary surfaces every data gap (below 20 cases, 0 holdout, low Tier-1,
  missing action_signals, high-leakage cases) so the gap can't be ignored.
- Observed outcomes are used only to score/validate — never as a model input.

## Minimum bar before calibration (Phase 15E) may begin

1. **20+ diverse cases** (across sources, categories, price models).
2. **A meaningful, clean holdout split** (new cases default to holdout).
3. **Tier-1/Tier-2 action outcomes** captured per case (not just comments).
4. **Repeated source/category patterns** confirmed across multiple cases.
5. **No single-case tuning** — every adjustment validated on holdout.

Until all five hold, no calibration is applied and no accuracy is claimed.

---

*Phase 15G is data infrastructure only. It does not change live forecasts,
apply calibration, or pretend Assembly is more accurate yet. It creates the
validation discipline that makes future calibration real.*
