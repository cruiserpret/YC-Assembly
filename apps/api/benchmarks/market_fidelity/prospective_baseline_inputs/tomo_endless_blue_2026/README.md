# Tomo: Endless Blue — frozen prospective baseline input (audit-only)

This directory holds the **single frozen input bundle** that every benchmarked method
must receive for the Tomo: Endless Blue prospective case, so that Assembly and the
GPT/Claude/Gemini + naive baselines are compared on **identical, pre-outcome evidence**.

| file | what it is |
|---|---|
| `input_bundle.json` | the **model-facing** frozen bundle — the exact founder brief Assembly forecast from, plus neutral case metadata + pre-lock source provenance. The ONLY thing any baseline sees. |
| `input_bundle_hash.txt` | the benchmark `input_bundle_hash` of `input_bundle.json` (every method locks against this same hash). |
| `provenance.json` | **audit-only, not model-facing** — where the bundle came from, the authenticity proof, the exclusions. |
| `baseline_prompt.md` | the single strict AMFB-v1 prompt (and its `prompt_hash`) sent to every plain-LLM baseline. |

## The same-frozen-evidence rule

Assembly locked its Tomo prediction on **2026-06-04** (run `4fcc4cbf…`,
`prediction_hash sha256:0a9ce639…`). The brief it forecast from is reproduced here
**verbatim**: `sha256(json.dumps(brief, sort_keys=True, separators=(',',':')))` equals
Assembly's git-committed `brief_hash sha256:4b188a0d…`. So this bundle is provably the
*same* input — not a reconstruction from the current web.

## Why current campaign data / the web are banned

A fair prospective benchmark requires every method to predict from the same pre-outcome
information. The model-facing bundle therefore **excludes** (see `input_bundle.json →
excluded_from_bundle`): the current/in-progress funding amount, current backer count,
live comments, any post-lock press, the final outcome (known on/after **2026-06-21**),
Assembly's own prediction, and Assembly's 144-item internal evidence-retrieval content.

## Status

- **Naive baselines:** locked (deterministic, zero-cost) — see
  `../../baseline_predictions/tomo_endless_blue_onibi_ks_2026__naive_*`.
- **GPT / Claude / Gemini baselines:** **PREPARED, NOT RUN.** No paid call was made.
  Lock them later (before 2026-06-21) via the gated preflight
  (`scripts/phase_17b_l_baseline_preflight.py`) with explicit approval + cost caps.
- These records carry `purpose: benchmark_*_not_validation_data`, are observed-free,
  and are **never** loaded by the validation ledger. Tomo is scored only on/after
  2026-06-21 — this directory adds no outcome.
