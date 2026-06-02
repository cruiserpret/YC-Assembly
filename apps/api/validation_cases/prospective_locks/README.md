# Prospective lock provenance — audit support, NOT validation data

Phase 16A-P. Each file here is a **compact, git-tracked provenance record** for one
prospective prediction lock (Phase 16A): a forecast Assembly produced and locked
**before** the outcome was known.

## What these records are
A small JSON capturing the audit-critical facts of a lock — `run_id`, the locked
`predicted_proportions`, the cryptographic hashes (`prediction_hash`, `brief_hash`,
`snapshot_hash`), `locked_at`, the hash parameters (`report_schema_version`,
`simulation_id_used_in_hash`), the cost summary, the run-quality + repair summary,
the evidence-snapshot source, and the future outcome date/metric.

## Why they exist
The full run artifacts (`founder_report.json`, etc.) live locally under
`apps/api/_audit/live_runs/<run_id>/` and are **untracked / not production-durable**.
These compact records make the lock **fully reproducible from git alone**: the
`prediction_hash` recomputes from the record's fields, so a later reviewer can prove
the prediction was not changed after the outcome — without the local artifact bundle.

## What they are NOT
- **NOT observed outcomes.** Every record has `observed: null` and carries no outcome
  data. Outcomes are recorded later **only** through the Phase 15L-C gated mapping path.
- **NOT official validation cases.** This directory is **deliberately absent from
  `manifest.json`**, so `loader.load_all_cases()` never reads it. `pending_cases.json`
  remains the single source of truth for the pending lock.
- **NOT calibration data.** Nothing here tunes or validates the model.

## Verify a record (read-only)
```bash
cd apps/api && export PYTHONPATH=src
python scripts/phase_16a_verify_lock_provenance.py \
    --record validation_cases/prospective_locks/<run_id>.json
```
It checks the record against `pending_cases.json` (run_id present, hash matches,
`observed` null, `used_for_holdout=true`, `used_for_training=false`, no action
signals, no observed-outcome fields) **and** recomputes the `prediction_hash` from
the record to confirm it is self-auditing. It writes nothing.

## Convention for future locks
Write a compact provenance record here **at lock time**; keep large local artifacts
outside git unless explicitly approved; for production runs set
`ASSEMBLY_ARTIFACT_ROOT` to the durable Railway volume. See
`docs/PHASE_16A_PROSPECTIVE_ACCURACY_SPRINT.md`.
