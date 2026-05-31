# Validation Case Review Checklist (Phase 15H)

> **Gate for ingestion.** A candidate case may only be promoted to `scored` /
> `holdout` (via `scripts/phase_15g_add_validation_case.py`) **after** an
> external reviewer has signed off on every applicable item below. This protects
> the ledger's anti-overfit and anti-leakage discipline. This checklist adds no
> accuracy and applies no calibration — it only enforces case quality.

Copy this file per case; fill in `[ ]` → `[x]` and the notes.

```
Case ID:            ____________________________
Candidate name:     ____________________________
Reviewer:           ____________________________
Review date:        ____________________________   (ISO YYYY-MM-DD)
Backlog target_id:  ____________________________   (if from acquisition_backlog.json)
```

## 1. Prediction lock
- [ ] A **locked prediction** exists with `prediction_lock.locked_prediction_created_at`
      set to the date the prediction was locked, **OR**
- [ ] (retrospective) a **clean-room reconstruction note** explains how the
      prediction was isolated from the already-known outcome.
- [ ] `run_id` / `evidence_snapshot_id` / `prediction_hash` recorded where available.
- Notes: ____________________________

## 2. Source / evidence snapshot
- [ ] The input evidence (source thread, comments, launch page) is captured or
      referenced by id/hash so the prediction is auditable.
- [ ] The snapshot pre-dates or matches the locked-prediction date.
- Notes: ____________________________

## 3. Observed outcome
- [ ] A **real observed outcome** exists (the four buckets sum to ~100).
- [ ] `observed.observed_at` (date the outcome was observed) recorded.
- [ ] The outcome is from a **credible, citable source** (not inferred/guessed).
- Notes: ____________________________

## 4. Denominator
- [ ] `denominator_type` set honestly (`comments` / `independent_voices` /
      `backers` / `upvotes` / `mixed_proxy` / `unknown`).
- [ ] `denominator_count` recorded where known.
- [ ] The denominator is **not** dressed up — comment-derived data is marked
      `comments`, not `backers`.
- Notes: ____________________________

## 5. Action signals
- [ ] `action_signals` populated where real revealed/semi-revealed action exists
      (Tier 1: purchase/backers/paid_signup/trial_conversion/install/github_fork;
      Tier 2: github_star/product_hunt_upvote/waitlist_signup/...), with
      `count` / `denominator` / `direction`, **OR**
- [ ] their **absence is honest** (no fabricated signals to inflate tier coverage).
- Notes: ____________________________

## 6. Leakage risk
- [ ] `leakage_risk` set **explicitly** (`low` / `medium` / `high`) — never `unknown`.
- [ ] If retrospective and the outcome was publicly known, risk is `medium`/`high`
      and the reasoning is documented.
- [ ] Confirmed: the prediction did **not** see the outcome
      (`observed_at` is **not** earlier than the locked-prediction date).
- Notes: ____________________________

## 7. Source / category classification
- [ ] `source_type` classified (`hacker_news` / `product_hunt` / `kickstarter` /
      `reddit` / `github` / `app_store` / `b2b` / `mixed`).
- [ ] `product_category` classified.
- [ ] Confirmed this case **adds diversity** (fills a source/category/tier gap)
      rather than over-concentrating one slice.
- Notes: ____________________________

## 8. Training / holdout split
- [ ] Split decided. **New cases default to `used_for_holdout = true` (blind).**
- [ ] Case is **not** both training and holdout (the schema forbids it).
- [ ] If marked training, the reason it already informed a model/dev decision is
      documented.
- Notes: ____________________________

## 9. Uncertainty notes
- [ ] `observation_confidence` set (`low` / `medium` / `high`).
- [ ] Known biases / caveats / measurement limitations recorded in notes.
- Notes: ____________________________

## 10. Reviewer sign-off
- [ ] All applicable items above are checked.
- [ ] The 15G CLI lock + leakage checks pass (or `--allow-partial` is justified
      and the case is flagged, **not** counted as clean holdout).
- [ ] I confirm this is **real, externally-reviewed** data — **no invented
      outcomes**.

```
Reviewer signature: ____________________________
Decision:           [ ] ready_to_ingest    [ ] needs_more_evidence    [ ] rejected
```

---

*This checklist is intake discipline only. It does not change any forecast,
apply calibration, or claim accuracy. It exists so that every case entering the
ledger is auditable, leakage-checked, and honestly graded.*
