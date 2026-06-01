# Validation Case Factory — candidate store (Phase 15J)

This directory holds **candidate** market-outcome leads, one JSON file per
candidate (`<candidate_id>.json`), produced by the Phase 15J factory CLI
(`scripts/phase_15j_candidate_factory.py`).

**A candidate is NOT a validation case.**

- This directory is **deliberately absent from `../manifest.json`**, so the
  validation-ledger loaders (`load_cases`, `load_all_cases`) **never** read it.
- Every candidate file carries `"purpose": "candidate_evaluation_not_validation_data"`
  and uses the `CandidateCase` schema (`extra="forbid"`), so it can never carry
  ledger-only fields (`observed` / `predicted` / `anti_overfit` / `metrics`) or be
  parsed as a `ValidationCase`.
- A candidate only becomes a ledger case after it passes the **human reviewer
  checklist + the factory hard gates** and is explicitly `ingest`-ed — which
  appends a reviewed case to `holdout_cases.json` / `training_cases.json` /
  `pending_cases.json` (never to the frozen `seed_cases.json`).

No invented data. No forecast change. No calibration. See
`docs/PHASE_15J_VALIDATION_CASE_FACTORY.md`.
