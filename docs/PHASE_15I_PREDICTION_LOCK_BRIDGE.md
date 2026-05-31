# Phase 15I — Prediction Lock Bridge

> **Auditability infrastructure only.** Phase 15I lets a *completed* Assembly run
> produce a validation-ledger `pending` case skeleton — recording **what
> Assembly predicted** and an auditable **prediction lock** — **without inventing
> any observed outcome**. It applies no calibration, changes no live forecast,
> adds no scored case, and claims **no** improved accuracy. It exists so that
> future validation cases are clean, leakage-proof, and reproducible.

Part of the Phase 15 roadmap, and the connective tissue between Phase 14C
(durable run artifacts), locked predictions, and the validation ledger
([15B](PHASE_15B_VALIDATION_LEDGER.md) · [15G](PHASE_15G_VALIDATION_DATASET_EXPANSION.md)
· [15H](PHASE_15H_VALIDATION_CASE_ACQUISITION_PLAN.md)).

## Why durable prediction locks matter

A validation case is only trustworthy if we can prove the prediction was fixed
**before** the outcome was known. Two things make that proof possible:

1. **The prediction is captured the moment the run completes** — folded into the
   four canonical market buckets and frozen, not reconstructed later from memory.
2. **A deterministic `prediction_hash` fingerprints exactly what was predicted**,
   so any later edit to the locked prediction is detectable. The hash is
   portable (path-free, machine-independent) and drift-proof (proportions are
   formatted to fixed decimals before hashing), so the *same* prediction always
   yields the *same* digest on any host.

Without this, a "locked prediction" is just a claim. With it, the lock is
evidence.

## Why this is required before paid *prospective* validation

The cleanest validation cases are **prospective**: lock a prediction now, observe
the real outcome weeks/months later. That only works if the locked prediction
survives — which is exactly what **Phase 14C** durable artifacts provide (set
`ASSEMBLY_ARTIFACT_ROOT` to a mounted Railway Volume so a completed run's
`founder_report.json` / `evidence_snapshot.json` are not wiped on redeploy).

Phase 15I is the bridge that turns those durable artifacts into a ledger case:

```
completed run  ──(14C durable artifacts)──▶  founder_report.json + evidence_snapshot.json
                                                        │  Phase 15I
                                                        ▼
                                    pending ValidationCase (predicted + prediction_hash,
                                    observed = null, used_for_holdout = true)
```

Paying for a prospective run only makes sense once its prediction can be locked
durably and bridged into the ledger. **Do not run paid prospective simulations
until Phase 14C is configured on a durable volume.**

## How to create a pending case from a completed run

```
cd apps/api
python scripts/phase_15i_create_case_from_run.py \
    --run-id <run-id> \
    --source-type product_hunt \
    --product-category developer_tools \
    [--case-id ...] [--locked-at <ISO>] [--leakage-risk low] \
    [--output validation_cases/pending_cases.json] [--allow-partial] [--print-only]
```

What it does (read-only over the run's artifacts; **no DB, no LLM, no network**):

1. Resolves the run's durable artifact dir via Phase 14C `run_artifact_dir`
   (honours `ASSEMBLY_ARTIFACT_ROOT`).
2. Reads `founder_report.json` and folds its `intent_distribution` into the four
   canonical buckets (`buyer_action_positive` / `receptive` /
   `uncertain_proof_needed` / `skeptical_resistant`, percentage points) using the
   **conservative, routing-independent** label→bucket mapping (reused from the
   calibration extractor, not re-derived). A run whose distribution is empty
   would yield a meaningless flat 25/25/25/25 prior — that is **refused**, not
   stored.
3. Reads `evidence_snapshot.json` (best-effort) for `brief_hash`,
   `evidence_snapshot_id`, `evidence_snapshot_hash`, and the lock timestamp.
4. Computes a deterministic `prediction_hash` (see below) and assembles a
   `pending` `ValidationCase`: `predicted` set, `observed = null`,
   `used_for_holdout = true`, `used_for_training = false`.
5. Runs the ledger's lock + leakage checks, then appends to
   `pending_cases.json` (refuses a duplicate `case_id`; refuses a missing
   prediction/lock unless `--allow-partial`, which stores a flagged `partial`
   skeleton instead).

`--print-only` builds and prints the case without appending it.

### The `prediction_hash`

`validation_ledger/prediction_lock.py::compute_prediction_hash` returns
`"sha256:" + sha256(canonical_payload)`, mirroring the repo's existing hash
idiom. The payload is an ordered, fixed field set:

- `hash_schema_version` (`"prediction_hash.v1"`)
- `run_id`
- `simulation_id` (None — no first-class per-run id is persisted)
- `predicted` (the four buckets, each formatted `f"{round(v,4):.4f}"`)
- `brief_hash`, `evidence_snapshot_id`, `evidence_snapshot_hash` (content hashes)
- `locked_prediction_created_at` (the **only** timestamp; never wall-clock)
- `model_version` (`{report_schema_version: ...}`)

It **never** includes filesystem paths, machine-local roots, or the current
time, so it is identical across machines and redeploys for the same prediction.

## How the observed outcome is added later

Phase 15I deliberately leaves `observed = null`. When the market actually reacts,
a human records the real outcome (purchases / backers / installs / paid signups /
etc.), externally reviewed per the
[15H checklist](../apps/api/validation_cases/templates/validation_case_review_checklist.md),
and promotes the case from `pending` to `scored`. Only a case that was locked
*before* its outcome — provable via the `prediction_hash` and
`locked_prediction_created_at` — counts as a clean holdout.

## How this prevents leakage

- The prediction is locked and hashed at case-creation time; `observed` is never
  read or written by this bridge — it cannot peek at an outcome it doesn't touch.
- `locked_prediction_created_at` is recorded so the ledger's existing
  no-peeking check (observed date must not precede the lock) can run later.
- New cases default to `used_for_holdout = true` (blind) and never to training;
  the schema forbids a case being both.
- A meaningless flat-prior "prediction" is refused, so empty runs cannot
  manufacture a fake locked prediction.

## Why this still does not claim accuracy

A locked prediction with no observed outcome is **not** evidence of accuracy — it
is evidence of *discipline*. Until the outcome is added and the case is scored on
data it never informed, nothing here says Assembly is more accurate. The dataset
reality is unchanged: the ledger still holds the same real cases, and the gate
(≥20 diverse cases, clean holdout, Tier-1/2 outcomes) still stands.

## How this feeds Phase 15E / 15F

15I produces the *supply* of clean, locked, prospective `pending` cases. As their
outcomes are observed and they become `scored` holdout cases, the dataset grows
toward the calibration gate. Only then:

- **Phase 15E** — a flag-gated calibrated prototype, alongside (never replacing)
  the current forecast, fit on training cases.
- **Phase 15F** — held-out scoring of that prototype vs the baseline on cases it
  never saw. **No calibration is applied and no accuracy is claimed until a
  held-out win.**

---

*Phase 15I is auditability infrastructure only. It adds no observed outcomes,
applies no calibration, changes no forecast, and claims no accuracy. It makes
future validation cases locked, leakage-proof, and reproducible.*
