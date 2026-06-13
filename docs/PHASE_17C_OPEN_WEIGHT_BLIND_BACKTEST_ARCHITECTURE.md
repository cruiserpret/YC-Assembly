# Phase 17C — Open-Weight Blind Backtest Architecture

**Status:** ARCHITECTURE / harness only. **No** real benchmark is run, **no** models are downloaded or
called, **no** paid provider calls, **no** survey spend, **no** Tomo scoring, **no** outcomes, **no**
forecast/calibration/prompt change, **no** Phase 13 / token change, **no** accuracy claim. This phase adds
the structure that *later* (17D/17E) will let Assembly run model-agnostically and prove it is an
architecture, not a wrapper — **without changing forecast behavior now.**

## Why Assembly must not be judged as "just Anthropic/GPT"

Assembly's claim is that its **protocol** (society construction, evidence, discussion, calibration,
locking) improves a base model's market-reaction forecast. If we only ever run Assembly on one frontier
model, a skeptic can say "that's just the model." So the benchmark is framed as an **architecture test**.

## Raw(base_model) vs Assembly(base_model) — the core comparison

For each base model we run two lanes on the **same frozen input bundle**:

> **Assembly Lift = Assembly(base_model) score − Raw(base_model) score**

(lower-is-better scores like Brier/MAE → positive lift means Assembly *improved* the model). Examples:
Raw Qwen vs Assembly(Qwen), Raw Llama vs Assembly(Llama), Raw Mistral vs Assembly(Mistral), and the same
for Claude/GPT/Gemini. `run_metadata.RunMetadata` describes any run model-agnostically (family, checkpoint,
provider, release date, cutoff, local/remote, web/rag/tools/assembly flags, temperature, seed, hashes,
contamination risk, blindness tier); `lift.verify_pairing` enforces that a Raw/Assembly pair shares the
**same `input_bundle_hash` and base model**.

## Why open-weight does NOT automatically mean blind

For a **retrospective** case, running a local/open-weight model offline removes *retrieval* leakage but
**not** leakage baked into the model's **pretrained weights** — the model may already "remember" the
outcome.

- **What CAN be zeroed — retrieval/vector/RAG leakage:** `retrieval_filter.filter_pre_outcome_evidence`
  excludes (sets `retrieval_weight=0`) any source that post-dates the prediction, post-dates the outcome,
  or visibly contains outcome values (postmortems: "raised $X", "final backers", "successfully funded",
  "failed", …), and emits an audit report + `evidence_bundle_hash`.
- **What CANNOT be guaranteed removed — pretrained model weights:** measured, not erased, by
  `knowledge_probe` (ask "what happened to / how much did X raise / succeed or fail?" *before* showing the
  case). If the model reveals outcome knowledge → the case is **not** public claim-grade.

## Blindness tiers (`blindness.py`)

| Tier | id | meaning | public claim? |
|---|---|---|---|
| **0** | prospective_clean | outcome hasn't happened; locked before outcome | **yes** (strongest) |
| **1** | time_frozen_model_clean | retrospective, but model checkpoint/release/cutoff predates the outcome; offline; no web/RAG/tools; pre-outcome bundle | **only with justified provenance** |
| **2** | open_weight_cutoff_uncertain | open-weight/local, offline, but cutoff/exposure uncertain | internal-only |
| **3** | closed_frontier_after_outcome | closed model used after the outcome exists | UX/report only |
| **4** | contaminated_or_post_outcome | live-web contamination / known post-outcome exposure | case-study only |

**Public benchmark claims may use ONLY Tier 0 and a carefully-justified Tier 1.** Tier 2 is internal-only;
Tier 3/4 cannot support accuracy claims. **Prospective validation remains strongest.**

## Offline-mode enforcement (`offline_policy.py`)

`validate_offline_blind_run_config(config)` hard-fails a blind run if `web_enabled` / `live_retrieval` /
`tools_enabled` are on, if `rag_enabled` without `frozen_evidence_bundle_only`, if the input bundle lacks
per-source timestamps, or if `outcome_date` / `prediction_timestamp` / model metadata are missing. The
disabled `adapters.py` stubs (Ollama / vLLM / llama.cpp / generic local) delegate to this and **raise** on
`generate_prediction` (no model load, no download, no call in 17C).

## Retrospective eligibility gate (`eligibility.py`)

`evaluate_eligibility(...)` combines the timestamps, model provenance (release/cutoff vs outcome), the
offline/contamination status, and the knowledge-probe verdict into a `blindness_tier` + a `classification`
+ `eligible_for_public_claim` with explicit reasons. It **downgrades** when: there are no pre-outcome
source timestamps; the model release/cutoff is at/after the outcome; the probe detects outcome knowledge
(→ Tier 4); live web was used after the outcome (→ Tier 4); or a retrospective case lacks temporal proof
(→ internal/case-study only). The conservative default is to downgrade.

## Audit records (`audit_records.py`)

`BacktestAuditRecord` captures `case_id`, baseline/paired record ids, model metadata, blindness tier,
contamination checks, the retrieval-filter + knowledge-probe reports, hashes, and the eligibility verdict.
Stored under `apps/api/benchmarks/market_fidelity/backtest_audits/` — **never** `validation_cases/`, never
loaded as a validation case, observed-free, immutable.

## Roadmap

- **Phase 17D —** build **historical case packs**: real resolved cases with **pre-outcome evidence
  bundles** (archived/timestamped sources), model-provenance metadata, and a run of the eligibility gate to
  assign each a blindness tier.
- **Phase 17E —** run **Raw Qwen vs Assembly(Qwen)** (and other open-weight models) on eligible cases,
  offline, behind an explicit flag + approval; compute Assembly Lift with confidence intervals.

## Isolation

Every module here imports only **stdlib + pydantic** (+ the 17B canonicalize/hash helpers). Nothing in
Assembly's forecast runtime imports the benchmark package; `load_all_cases()` is unchanged (8); audit
records are never validation cases; Phase 15E stays blocked. **No forecast behavior changes in 17C.**
