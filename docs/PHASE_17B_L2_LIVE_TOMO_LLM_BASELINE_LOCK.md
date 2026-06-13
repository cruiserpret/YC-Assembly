# Phase 17B-L2 — Approval-Gated Live GPT/Claude/Gemini Tomo Baseline Lock

**Status (this run): PREPARED_NOT_RUN.** No live provider call was made; no money was spent; no record was
written. The executor + adapters + gate + tests are built and ready, but the environment has **no provider
API keys** and **no approval flag**, so the fail-closed gate (correctly) declined to call.

**No** Tomo scoring, **no** current-campaign fetch, **no** input-bundle enrichment, **no** web/search,
**no** forecast/calibration/prompt/Phase-13/token change, **no** accuracy claim.

## Why this phase exists

The five naive Tomo baselines are locked (17B-L). To make the prospective comparison meaningful we also want
one **GPT**, one **Claude**, and one **Gemini** raw baseline, locked **before the outcome (2026-06-21)** on
the **exact same** frozen bundle. Those are paid calls, so this phase builds a tightly gated executor and only
spends under explicit, multi-factor approval.

## The exact frozen bundle (fairness anchor)

Every live baseline must lock against **`input_bundle_hash = sha256:f29e8a46e0a677e0985e606f643e49fbc63822402d3dbf2c0570be5be2dd5d01`**
(`apps/api/benchmarks/market_fidelity/prospective_baseline_inputs/tomo_endless_blue_2026/input_bundle.json`).
The executor recomputes the hash, asserts it equals this constant **and** the operator's
`--confirm-input-bundle-hash`, and refuses on any mismatch.

## Fail-closed approval gate

`scripts/phase_17b_l2_lock_live_llm_tomo_baselines.py` makes a provider call **only if ALL** hold; otherwise
it returns **PREPARED_NOT_RUN** (recoverable) or **BLOCKED** (hard safety failure) and calls nothing:

| factor | requirement |
|---|---|
| env approval | `ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true` |
| explicit CLI consent | `--i-understand-this-costs-real-money` |
| global cost cap | `--max-total-usd N` (> 0) |
| per-provider cap | `--max-per-provider-usd M` (> 0, ≤ N) |
| bundle confirmation | `--confirm-input-bundle-hash sha256:f29e8a46…` == the bundle's actual hash |
| pre-outcome window | the **REAL** clock is **before** 2026-06-21 (there is no operator date flag — it cannot be spoofed) |
| output location | the records dir path ends in `benchmarks/market_fidelity/baseline_predictions` |
| key presence | the provider's API key env var is set (else that provider is `blocked_missing_api_key`) |

A **hash mismatch, leakage, prompt leak, post-outcome real date, unreadable provenance, or out-of-tree output
dir is a hard BLOCK (rc=1)**. Missing approval/caps/confirmation is the expected **PREPARED_NOT_RUN (rc=0)**.

**Cost caps are enforced by worst-case reservation,** not by adapter-reported cost (these SDKs do not return a
reliable per-call cost): each called provider reserves the full `--max-per-provider-usd` against
`--max-total-usd`, so at most `floor(max_total / max_per_provider)` providers run. **To run all three, set
`--max-total-usd` ≥ 3 × `--max-per-provider-usd`.** Suggested: `--max-total-usd 6 --max-per-provider-usd 2`.

```bash
# the full gated command (only run when you intend to spend real money, before 2026-06-21):
ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true PYTHONPATH=src .venv/bin/python \
  scripts/phase_17b_l2_lock_live_llm_tomo_baselines.py \
  --input-bundle benchmarks/market_fidelity/prospective_baseline_inputs/tomo_endless_blue_2026/input_bundle.json \
  --providers openai anthropic google --max-total-usd 6 --max-per-provider-usd 2 \
  --confirm-input-bundle-hash sha256:f29e8a46e0a677e0985e606f643e49fbc63822402d3dbf2c0570be5be2dd5d01 \
  --i-understand-this-costs-real-money
```

## Provider adapters (isolated)

GPT / Claude / Gemini adapters live in the script (not in the benchmark package, not imported by Assembly
runtime). The SDK is imported **lazily inside the adapter** — importing the module loads no SDK and reads no
API key — and **no web/search/tool grounding** is enabled on any call. Adapters are injectable, so tests use
fakes and **no real provider call happens in tests**. Point-in-time model hints (`gpt-5.5`, `claude-opus-4-8`,
`gemini-3.5-flash`) are **re-verified at run time**: when the SDK echoes a resolved model id the record stores
it and `notes.model_id_source = "response"`; if a provider's SDK does not echo one (or the call failed) the
record stores the hint with `notes.model_id_source = "hint_unverified"` — it never over-claims verification.

## Records written (only when approved)

One immutable `BaselinePredictionRecord` per provider: `method_class=plain_llm`,
`method_id ∈ {gpt_raw_baseline, claude_raw_baseline, gemini_raw_baseline}`, `method_version = exact runtime
model id`, `input_bundle_hash = sha256:f29e8a46…`, `prediction_payload`, `prediction_hash`, `locked_at`,
`cost_usd`, `runtime_seconds`, `mode = live_provider_call`, `leakage_status = clean_pre_outcome`,
`observed = null`, `schema_failure` if the model refused / could not produce the schema, and `notes` (schema
mode, cost-reporting status, model id + source). Records are **self-verifying** (the hash must reproduce) and
**immutable** (a duplicate run refuses, recorded `blocked_immutable_exists`).

## Cost accounting

The cap is enforced deterministically by **reservation**: before each call the executor reserves the full
`--max-per-provider-usd` against the running total; a provider whose reservation would exceed `--max-total-usd`
is skipped and recorded `blocked_by_cost_cap`. So **at most `floor(max_total / max_per_provider)` providers
ever run**, independent of what the SDK reports. The record's numeric `cost_usd` is the exact cost when the SDK
returns one, else `0.0` with `cost_reporting_status = unavailable_sdk_no_cost` in `notes` (the run summary
prints the reservation total and an explicit note, not a misleading `$0`).

## Safety guarantees

No Tomo scoring, no outcome added, no current-campaign/web data, no Assembly prediction or naive output shown
to a baseline, records immutable and **not** validation cases (never loaded by the ledger). Tomo stays
`pending` / `observed=null` / `action_signals=[]`; ledger 8/6/2/2; Phase 15E blocked. No accuracy claim.

## Hardening (6-agent adversarial review)

`leakage_fairness`, `isolation_adapters`, and `tests_docs` held. The spend-safety defects the review found
were fixed: (1) the global cap is now enforced by **worst-case reservation** (the old code relied on
adapter-reported cost, which these SDKs return as `None`, so the cap was a no-op); (2) the pre-outcome gate now
uses the **real clock only** — the operator-spoofable `--now` flag was removed (tests inject a non-CLI
`now_real`); (3) the records-dir guard is now a **path** check (`…/market_fidelity/baseline_predictions`), not
a basename; (4) the Gemini/model-id path no longer over-claims verification (`model_id_source`); (5) a present-
but-unreadable `provenance.json` is a **hard block** (the numeric-leak guard fails closed). The cost-cap,
missing-key, immutable, boundary (06-20 locks / 06-21 blocks), and corrupt-provenance cases are regression-tested.

## Scoring

Tomo (Assembly + naive + these LLM baselines) is scored **only on/after 2026-06-21**, via the Phase 15L-C
gated mapping (buyer-anchor only). This phase locks baselines; it does not score.
