# `benchmarks/market_fidelity/` — Assembly Market Fidelity Benchmark (placeholder)

**Design placeholder only. No code. NOT runtime-loaded.** This directory reserves the future home of the
**Assembly Market Fidelity Benchmark v1 (AMFB-v1)** harness (Phase 17B+). As of Phase 17A it contains
**only this README** — there is no Python here, nothing imports `benchmarks/`, and nothing here is a model
input, a forecast, or a `validation_cases/` manifest entry. It changes no behavior.

## What this benchmark is

A head-to-head test of **predictive market fidelity**: *which method best predicts real future market
outcomes from the same pre-outcome evidence, locked before the outcome and scored after?* Methods compared:
Assembly, plain LLM baselines, AI startup-validation tools, survey/concept-testing platforms, human expert
panels, and naive/statistical baselines.

- **Spec:** `docs/PHASE_17A_ASSEMBLY_MARKET_FIDELITY_BENCHMARK_SPEC.md`
- **Prediction schema draft:** `docs/PHASE_17A_BENCHMARK_SCHEMA_DRAFT.json`

## Principle

*Same evidence, same timestamp, same schema, locked before outcome, scored after outcome.* Primary ranking
is by **strictly proper scoring rules** (Brier / log / CRPS); MAE / TVD / RMSE are descriptive secondaries;
calibration (ECE/ACE) and skill-vs-naive-baselines are reported with bootstrap CIs.

## Isolation / safety

- Never loaded by `assembly.validation_ledger` (manifest-driven over `validation_cases/`).
- No paid Assembly predictions, no paid survey panels, no model/forecast/calibration change is implied by
  this design.
- A real harness (Phase 17B+) runs only under an explicit cost gate + approval, and locks every
  prediction (input-bundle + prediction hash) before any outcome is knowable — mirroring Assembly's
  existing `prediction_hash` discipline.

## Roadmap

17B harness (LLM + naive baselines) → 17C survey/human-panel template → 17D competitor-tool capture →
17E first prospective batch (cost-approved) → 17F leaderboard/report.
