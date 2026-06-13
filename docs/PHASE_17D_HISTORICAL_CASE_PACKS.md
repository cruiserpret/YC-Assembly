# Phase 17D — Historical Case Pack Builder

**Status:** data-pipeline / architecture only. **No** models run or downloaded, **no** paid provider
calls, **no** live benchmarks, **no** Tomo scoring, **no** outcomes added to validation cases, **no**
forecast/calibration/prompt change, **no** Phase 13 / token change, **no** accuracy claim. This phase
builds the pipeline that creates **resolved historical case packs** so Phase 17E can later run
`Raw(Qwen)` vs `Assembly(Qwen)` on them **without leakage**.

## Why historical case packs exist

The moat test is `Raw(base_model)` vs `Assembly(base_model)`. To run it cheaply and repeatably we need
**already-resolved** cases. But a retrospective backtest is only meaningful if the realized outcome never
leaks into the prediction input. A case pack makes that separation **structural and auditable**.

## The strict separation (the core invariant)

Each pack has three independent artifacts:

1. **`input_bundle`** — the **only** thing shown to `Raw(model)` and `Assembly(model)`. **Strictly
   pre-outcome** evidence: per-source timestamps, content hashes, no final numbers, no postmortems, no
   "raised $X / failed / succeeded / final backers". A source published after the prediction (or after the
   outcome), or one whose text reveals the outcome, is **excluded** by the leakage audit
   (`retrieval_weight = 0`).
2. **`outcome_record`** — the realized result, kept in a **separate** file, used **only after the
   prediction is locked**, for scoring. A buyer/action numerator alone **must not** fabricate the
   non-buyer buckets — only a directly-defensible measurement may carry a full four-bucket distribution
   (`scoring_mapping_type = direct_observed_distribution`).
3. **provenance/audit** — source manifest (timestamp confidence), leakage audit, blindness tier,
   contamination risk, eligibility verdict, hashes.

## Pipeline (`pack_builder.build_case_pack`)

`input_bundle + outcome_record + candidate_metadata + provenance` →
**source manifest** (per-source timestamp confidence; an *accessed-only* fetch date is **not** publication
proof and downgrades) →
**leakage audit** (reuses the 17C `retrieval_filter`: excludes post-prediction / post-outcome / outcome-
revealing sources; `input_bundle_clean` true/false) →
**hashes** (`input_bundle_hash`, `outcome_record_hash`, `source_manifest_hash`, `full_case_pack_hash` —
deterministic + sensitive, via the 17B canonicalize/hash-lock) →
**eligibility** (the 17C gate → blindness tier + public-claim) →
**conservative classification**:

| case_status | when |
|---|---|
| **rejected** | outcome leakage in the bundle / post-outcome source / Tier-4 / unclean bundle |
| **case_study_only** | no defensible scoreable outcome, or Tier 2/3 (open-weight cutoff uncertain / model after outcome) |
| **candidate** | Tier 1 but provenance not yet justified |
| **accepted** | clean + scoreable + Tier 0, or Tier 1 with justified temporal/model provenance |

## Why historical backtests do NOT instantly prove accuracy

Open-weight is **not automatically blind**: the retrieval layer can be cleaned (we zero leaky sources),
but a base model may still **remember** a famous outcome from its pretrained weights. That is measured by
the 17C knowledge probe and bounded by the blindness tier. So **public claims may use only Tier 0 and a
carefully-justified Tier 1**; everything else is internal/case-study only. **Prospective validation
remains strongest** — these packs complement it, they don't replace it.

## How packs feed Phase 17E

17E loads **accepted** (and justified Tier-1) packs, hands each pack's `input_bundle` (only) to
`Raw(Qwen)` and to `Assembly(Qwen)`, hash-locks both predictions (17B), then scores them against the
pack's `outcome_record` and reports **Assembly Lift** (lower-is-better → `raw_error − assembly_error`) with
confidence intervals — offline, behind an explicit flag + approval.

## Why candidate diversity matters

A pack list of only famous winners is worthless (cherry-pick + memorization skew). `case_registry`
requires a balanced spread (successes **and** failures, ≥1 middling/uncertain, ≥2 categories, not all
famous) and warns otherwise.

## Dry-run CLI

```bash
cd apps/api
# dry-run (prints classification + audit; writes NOTHING)
PYTHONPATH=src .venv/bin/python scripts/phase_17d_create_historical_case_pack.py \
  --metadata benchmarks/market_fidelity/historical_case_packs/fixtures/01_clean_accepted_tier1.json
# persist (only into historical_case_packs/<status>/<case_id>/)
PYTHONPATH=src .venv/bin/python scripts/phase_17d_create_historical_case_pack.py --metadata <file> --write
# validate
PYTHONPATH=src .venv/bin/python scripts/phase_17d_validate_historical_case_pack.py --metadata <file>
```

## Isolation

The `historical_cases` subpackage imports only **stdlib + pydantic** (+ the 17B/17C benchmark helpers).
It does **not** import any model/heavy/SDK/network library (pydantic transitively loads `_socket`, but no
socket is ever opened and there are no network-call primitives), **nothing in Assembly imports it**, packs
are stored under `apps/api/benchmarks/market_fidelity/historical_case_packs/` and are **never** loaded as
validation cases, `load_all_cases()` stays 8, and Phase 15E is unaffected.

## Fixtures (synthetic — not real claims)

`historical_case_packs/fixtures/`: `01_clean_accepted_tier1` (accepted/Tier 1), `02_post_outcome_leakage`
(rejected), `03_action_anchor_only` (accepted), `04_direct_observed_distribution` (accepted),
`05_missing_timestamp_downgrade` (case_study_only/Tier 2), `06_famous_memorization_risk` (rejected/Tier 4).
These are **synthetic** test data; **no public accuracy claim** is made from them.
