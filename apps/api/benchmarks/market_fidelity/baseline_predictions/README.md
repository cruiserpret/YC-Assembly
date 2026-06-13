# `baseline_predictions/` — locked benchmark baseline records (audit-only)

Immutable, hash-locked benchmark predictions (one `<case>__<method>__<digest>.json`
per locked prediction), written by `scripts/phase_17b_lock_baseline_prediction.py`
**only** with the explicit `--write` flag.

**Isolation / safety:**
- `purpose: benchmark_baseline_prediction_not_validation_data`.
- **Never** loaded by `assembly.validation_ledger` (manifest-driven over
  `validation_cases/`) — these are **not** validation/training/holdout cases.
- Each record is **observed-free at lock time**; outcomes are added only by a later
  scoring phase, never at lock.
- Records are **immutable** (the writer refuses to overwrite an existing file).

As of Phase 17B this directory is **empty** (only this README) — no baseline has been
locked yet, and no paid provider calls exist in 17B. See
`docs/PHASE_17B_BENCHMARK_HARNESS.md`.
