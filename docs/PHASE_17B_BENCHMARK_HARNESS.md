# Phase 17B — Benchmark Harness (LLM + naive baselines)

**Status:** executable INFRASTRUCTURE only. **No** benchmark is run. **No** paid LLM calls, **no**
survey spend, **no** competitor-tool runs, **no** live provider baseline locks, **no** Tomo scoring,
**no** outcomes, **no** forecast/calibration/prompt change, **no** Phase 13 / token change, **no**
accuracy claim. This phase builds the harness that *later* (Phase 17B-L, under explicit approval + cost
gate) will lock baseline predictions for the AMFB-v1 benchmark (`docs/PHASE_17A_..._BENCHMARK_SPEC.md`).

## What 17B ships

A self-contained, isolated package — `assembly.benchmarks.market_fidelity` — that can lock baseline
predictions from **(1) naive/statistical baselines** and **(2) manually-pasted/file LLM outputs**, under
fair-comparison discipline: same input bundle, same schema, canonical JSON, hash-locked, immutable
records, no post-outcome edits, no leakage.

| File | Role |
|---|---|
| `schema.py` | `BenchmarkPrediction` (4 buckets sum to ~100 unless `schema_failure`; `confidence` required) |
| `canonicalize.py` | deterministic canonical JSON (sorted keys, fixed float precision) |
| `hash_lock.py` | `input_bundle_hash` + `compute_prediction_hash` (SHA-256 over the canonical lock payload) |
| `baseline_records.py` | `BaselinePredictionRecord` (immutable, observed-free, purpose-marked) + writer/loader |
| `naive_baselines.py` | `always_zero_buyer`, `majority_receptive`, `uniform_distribution`, `category_prior_placeholder`, `crowdfunding_goal_progress_placeholder` |
| `metrics.py` | `bucket_mae`, `tvd`, `rmse`, `brier_multiclass`, `brier_binary`, `directional_hit`, schema-failure accounting |
| `validators.py` | schema validation, offline-mode guard, pre-outcome leakage check |
| `providers.py` | GPT/Claude/Gemini adapter **stubs — disabled** (`ProviderCallDisabledError`); no SDKs, no keys, no network |
| `scripts/phase_17b_lock_baseline_prediction.py` | the lock CLI (dry-run by default; `--write` to persist) |

Locked records are written under `apps/api/benchmarks/market_fidelity/baseline_predictions/` — **never**
under `validation_cases/`.

## How to dry-run (writes nothing)

```bash
cd apps/api
# naive baseline
PYTHONPATH=src .venv/bin/python scripts/phase_17b_lock_baseline_prediction.py \
  --case-id tomo_endless_blue_onibi_ks_2026 \
  --method-id naive_uniform --method-class naive_baseline --method-version v1 \
  --input-bundle path/to/frozen_input_bundle.json --naive uniform_distribution

# manually-pasted LLM output (no provider call — you paste the JSON the model produced)
PYTHONPATH=src .venv/bin/python scripts/phase_17b_lock_baseline_prediction.py \
  --case-id tomo_endless_blue_onibi_ks_2026 \
  --method-id gpt_manual_baseline --method-class plain_llm --method-version "manual_placeholder" \
  --input-bundle path/to/frozen_input_bundle.json --prediction-json path/to/prediction.json
```

The CLI **validates** the schema, **canonicalizes**, computes the **input-bundle hash** + **prediction
hash**, prints the immutable lock record, and — by default — **writes nothing**. It persists only when
you add `--write` (and only into the benchmark records dir; it refuses to overwrite an existing record).

## How manual LLM outputs are locked later (without a paid call in 17B)

You run the model yourself (web UI, your own key, whatever) against the **frozen input bundle**, force it
to the AMFB-v1 schema, paste the resulting JSON into a file, and lock it with `--prediction-json`. The
harness never calls a provider; it only validates + canonicalizes + hashes what you give it. This keeps
17B **zero-spend** while still producing a fair, hash-locked baseline.

## Why there are NO paid provider calls in 17B

`providers.py` ships disabled stubs: `LIVE_PROVIDER_CALLS_ENABLED = False`, every `lock_prediction()`
raises `ProviderCallDisabledError`, there are no SDK imports and no API-key reads, and the CLI only
supports `--naive` / `--prediction-json` (mode `naive` / `manual_output`). The `future_provider_call`
mode is explicitly **refused** by `assert_mode_is_offline`. This guarantees the build cannot spend money.

## Phase 17B-L (later, behind explicit approval)

A follow-up phase will add the live provider adapters (GPT-5.5 / Claude / Gemini, re-verified at run
time) and **run + lock** those baselines — plus the naive baselines — for **Tomo** against the **frozen
Tomo evidence snapshot**, **before 2026-06-21**, so every method is locked pre-outcome. That phase runs
**only** under an explicit live-calls flag + the project cost gate + per-run approval. **17B contains
none of that.**

## How this avoids leakage

- Every method is locked against the **same `input_bundle_hash`** (identical frozen evidence).
- Predictions are **hash-locked before** the outcome (commit-then-reveal); records are **immutable** and
  **observed-free** at lock time (outcomes are added only by a later scoring phase).
- `check_no_post_lock_sources` rejects any search-assisted source whose `retrieved_at` post-dates the
  lock.
- Tomo's lock and outcome are **untouched** by this phase; the benchmark is scored only **after** the
  real outcome resolves (on/after 2026-06-21), never early.

## Isolation

`assembly.benchmarks.market_fidelity` imports only **stdlib + pydantic**. It does **not** import the
forecast runtime, calibration, config, or the validation ledger, and **nothing in Assembly imports it**.
`load_all_cases()` is unchanged (still 8), the benchmark records are not validation cases, and Phase 15E
is unaffected (still blocked). A test (`test_package_does_not_import_ledger_or_runtime`) enforces this.
