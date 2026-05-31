# Phase 15B — Validation-Case Ledger

This is the **foundation** of the Phase 15 calibration roadmap
([scope](PHASE_15_MARKET_EVIDENCE_AND_CALIBRATION_SCOPE.md)). It is
**data + deterministic metrics only** — no calibration, no learned ML, no
forecast changes, no LLM, no network, no production-simulation logic. It is
the measuring stick everything else in Phase 15 will be held to.

## What the ledger is

A structured, version-controlled dataset of real product launches. Each
**validation case** records:

- **Metadata** — product, source channel, category, launch stage, date,
  validation status, confidence.
- **A locked prediction** — what Assembly forecast *before* the outcome was
  known, with audit references (`run_id`, evidence snapshot, hashes,
  leakage-risk note) so the prediction can't be quietly back-fitted.
- **Predicted proportions** — the four-bucket market split Assembly produced:
  buyer/action-positive · receptive · uncertain/proof-needed ·
  skeptical/resistant.
- **Observed proportions** — what the market actually did, with the
  *denominator* it was measured against (comments, independent voices,
  backers, upvotes, mixed proxy…) and an honest observation-confidence.
- **Metrics** — computed deterministically (MAE in pp, TVD, max-bucket
  error, signed bucket errors, direction match, buyer-false-confidence).
- **Failure analysis** — which bucket was missed / over- / under-predicted,
  root-cause tags, source-bias and category-prior notes.
- **Anti-overfit bookkeeping** — `used_for_training` vs `used_for_holdout`.

Code: `apps/api/src/assembly/validation_ledger/` (`schema.py`, `metrics.py`,
`loader.py`). Data: `apps/api/validation_cases/seed_cases.json`.

## Why it matters

Assembly's north star is **accurate market proportions with confidence**, not
smarter-sounding agents. You cannot calibrate — or even *claim* — accuracy
without a held-out dataset of locked predictions vs real outcomes. Every
future Phase 15 step (source-bias profiles, action-signal weighting, category
priors, the calibrated forecast prototype) is meaningless until it can be
**scored on this ledger**. Build the measuring stick first.

The seed already pays off: loading the six current cases reproduces the known
**no-behavioral baseline of avg MAE ≈ 25.30 pp**, and the per-case failure
analysis surfaces a clear, generalizable pattern —

- **Hacker News** cases (DocuSeal, Files.md, Opslane): the model
  **over-predicts receptive** and **under-predicts skeptical / proof-needed**.
- **Product Hunt / Kickstarter** cases (HasData, Naptick, Tiiny): the model
  **over-predicts skeptical** and **under-predicts buyer / uncertain**.

That is a **source-bias signal**, visible only because predictions and
outcomes are recorded side by side — exactly what Phase 15D (source profiles)
will correct.

## How it prevents overfitting

- **Locked predictions.** Each case stores the prediction's audit references
  and a `leakage_risk` flag, so a forecast can't be silently tuned after the
  outcome is seen.
- **No outcome data as model input.** Observed proportions are used *only* to
  score predictions — never fed back into any model. The metrics module is
  pure measurement.
- **Training vs holdout split** (below) keeps a portion of cases unseen, so
  calibration quality is reported on data the model never trained on.
- **`used_for_training` and `used_for_holdout` can never both be true** — the
  schema rejects it. A case is one or the other, never both.

## Training vs holdout classification

- A case is **training** (`used_for_training=true`) if its outcome has
  informed any model/calibration decision — it has been "seen."
- A case is **holdout** (`used_for_holdout=true`) if it is reserved, unseen,
  to score generalization. Holdout cases must not influence any tuning.
- The schema forbids a case being both.

**The six seed cases are all marked `used_for_training=true`, `used_for_holdout=false`.**
They were used during Phase 12/13 development (the behavioral rev4 experiment
saw them), so they are **not** a clean holdout. Honest calibration claims must
come from *new* holdout cases added later — see below.

## How future calibration will use it

- **Phase 15D** estimates per-source profiles + per-category priors on the
  **training** split, then reports error on the **holdout** split.
- **Phase 15E** assembles the calibrated forecast pipeline behind a flag.
- **Phase 15F** scores the calibrated forecast against the uncalibrated
  baseline on the **holdout** ledger, using the metrics here. Only a held-out
  win authorizes surfacing calibrated numbers to founders.
- The same ledger is the promotion gate for the parked behavioral layer
  ([Phase 13E](PHASE_13E_BEHAVIORAL_SHADOW_MODE_DESIGN.md)) — build it once,
  reuse it.

The loader exposes `load_cases`, `compute_case_metrics`, `training_cases`,
`holdout_cases`, `scored_cases`, and `ledger_summary` for all of the above.

## How to add new validation cases

1. Append an object to `apps/api/validation_cases/seed_cases.json` (the schema
   validates it on load; malformed cases fail loudly).
2. **Lock the prediction first.** Fill `prediction_lock` (`run_id`, evidence
   snapshot id/hash, date) *before* recording the outcome, and set
   `leakage_risk` honestly.
3. Record `observed` with its real **denominator** and an honest
   `observation_confidence`. **Do not invent precision** — use `null` fields
   and `partial`/`pending` status when the data is incomplete.
4. **Default new cases to `used_for_holdout=true`** (unseen) unless they have
   already influenced a model decision — growing a clean holdout set is how
   Assembly earns the right to claim calibrated accuracy.
5. Aim for **diversity over count** — span sources, categories, and pricing
   models. Twenty dev tools is not a validation set; target 20+ diverse
   blind launches.

> Note: GraphNest/Atomic are intentionally **not** seeded — the repo does not
> currently hold locked evidence + observed proportions for them. They can be
> added later as `pending`/`partial` once that data exists; do not fabricate
> observed numbers.

## Why comments are not the whole market

Public comment threads are a **biased, vocal, performative sub-sample**. They
over-represent the opinionated and the in-category and under-represent
everyone who acted quietly. Hacker News skews skeptical; Product Hunt skews
enthusiast; Kickstarter skews early-adopter. The ledger's `denominator_type`
field forces honesty about *what population* an observed proportion was
measured against — a comment-thread proportion is not a market proportion, and
the ledger refuses to pretend otherwise.

## Why action signals matter

Comments are strong for **"why"** (objections, proof needs) and weak for
**"how many."** Real **actions** — purchases, backers, signups, installs,
GitHub stars — are the strongest proxy for proportions. The Tiiny case makes
this concrete: the model predicted 0% buyers, but Kickstarter **backers**
(an action) are buyers, and the observed buyer share was ~30%. Future phases
(15C action-signal weighting) will weight expressed signal by its action
tier; the ledger's `denominator_type` + per-case failure analysis are where
that need is recorded today.

---

*Phase 15B is data + metrics only. No forecast, model, calibration, behavioral,
token, or production-simulation logic is changed by it.*
