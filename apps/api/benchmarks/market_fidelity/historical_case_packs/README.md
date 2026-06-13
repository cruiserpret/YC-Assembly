# `historical_case_packs/` — resolved historical case packs (audit-only)

Each pack (under `accepted/`, `rejected/`, or `candidates/`) is a directory
`<case_id>/` holding the **separated** artifacts:

- `input_bundle.json` — the ONLY thing shown to Raw(model) / Assembly(model): strictly
  **pre-outcome** evidence (no final numbers, no postmortems).
- `outcome_record.json` — the realized result, used **only after the prediction is
  locked**, for scoring. Never a model input.
- `source_manifest.json` · `leakage_audit.json` · `eligibility_report.json` — provenance.
- `case_pack.json` — the top-level `HistoricalCasePack` (hashes, blindness tier,
  eligibility, candidate diversity metadata).

**Isolation / safety:**
- `purpose: historical_case_pack_not_validation_data`.
- **Never** loaded by `assembly.validation_ledger` — these are **not** validation cases,
  and they do not touch Phase 15E.
- Written only with the CLI's explicit `--write` (dry-run by default); immutable.
- `fixtures/` holds synthetic test packs only — **not real claims**.

As of Phase 17D the non-fixture dirs are empty (only READMEs). No model is run/downloaded.
See `docs/PHASE_17D_HISTORICAL_CASE_PACKS.md`.
