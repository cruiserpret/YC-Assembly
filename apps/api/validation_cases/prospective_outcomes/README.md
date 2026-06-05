# `prospective_outcomes/` — partial outcome records (audit support, NOT validation data)

Each `run_<run_id>.json` here records the **partial, buyer/action-anchor outcome** of a prospective
lock (Phase 16B-R), for a case whose full four-bucket `observed` distribution is **not** observable.

These files are **audit support only**:

- `purpose: prospective_partial_outcome_not_observed_distribution`.
- This directory is **deliberately ABSENT from `manifest.json`** and is **never loaded as a validation
  case** (the loader is manifest-driven) — exactly like `prospective_locks/`, `prospective_targets/`,
  `candidates/`, and `mapping_proposals/`.
- They carry **no** four-bucket observed distribution. `observed` stays null on the case; the
  non-buyer buckets (receptive / uncertain_proof_needed / skeptical_resistant) are *unobserved*, not
  invented.
- They are **not** `direct_observed_distribution` and do **not** count toward the Phase 15E
  ≥20-direct-observed bar.
- The matching case in `pending_cases.json` is moved to `validation_status: "partial"` and carries the
  buyer anchor in `action_signals`; its `prediction_lock` / `prediction_hash` are unchanged.

Verify a record (read-only):

```
cd apps/api && PYTHONPATH=src \
  python scripts/phase_16b_verify_prospective_partial_outcome.py \
  --record validation_cases/prospective_outcomes/run_<run_id>.json
```

See `docs/PHASE_16B_PROSPECTIVE_PARTIAL_OUTCOME_CONVENTION.md`.
