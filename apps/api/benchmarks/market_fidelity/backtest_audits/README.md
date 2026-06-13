# `backtest_audits/` — open-weight blind-backtest audit records (audit-only)

One immutable `<case>__<baseline>.json` per benchmarked (case, baseline) capturing the
full blindness/contamination provenance: model metadata, blindness tier, contamination
checks, the retrieval-filter report, the knowledge-probe report, hashes, and the
public-claim eligibility — written by the Phase 17C architecture only with an explicit
opt-in.

**Isolation / safety:**
- `purpose: benchmark_backtest_audit_not_validation_data`.
- **Never** loaded by `assembly.validation_ledger` — these are **not** validation cases.
- **Observed-free** (no outcome is written here).
- **Immutable** (the writer refuses to overwrite).

As of Phase 17C this directory is **empty** (only this README) — no backtest has been run;
no models are downloaded or called in 17C. See
`docs/PHASE_17C_OPEN_WEIGHT_BLIND_BACKTEST_ARCHITECTURE.md`.
