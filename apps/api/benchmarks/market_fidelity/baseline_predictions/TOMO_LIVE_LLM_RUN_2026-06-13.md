# Tomo live LLM baseline lock — run audit (2026-06-13)

Audit note for the live GPT/Claude/Gemini raw baseline lock run via the merged Phase 17B-L2
executor (`scripts/phase_17b_l2_lock_live_llm_tomo_baselines.py`). **No Tomo scoring, no web,
no bundle change, no accuracy claim.**

## Run facts
- **Real run date:** 2026-06-13 (before the Tomo outcome window 2026-06-21). The records'
  `locked_at` is the frozen bundle's timestamp (2026-06-04), by convention.
- **Frozen input bundle:** `prospective_baseline_inputs/tomo_endless_blue_2026/input_bundle.json`,
  `input_bundle_hash = sha256:f29e8a46e0a677e0985e606f643e49fbc63822402d3dbf2c0570be5be2dd5d01`
  (preflight: hash match, leakage CLEAN, prompt CLEAN, all keys present, before outcome window).
- **Approval / caps:** explicit operator approval; `--max-total-usd 6 --max-per-provider-usd 2`,
  `ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true`, `--confirm-input-bundle-hash` matched.

## Baselines locked (all `plain_llm`, `mode=live_provider_call`, `observed=null`, self-verifying)
| method | runtime model id (`model_id_source=response`) | status | prediction_hash |
|---|---|---|---|
| `gpt_raw_baseline` | `gpt-5.5-2026-04-23` | locked | `sha256:a2643eb657c3…` |
| `claude_raw_baseline` | `claude-opus-4-8` | locked | `sha256:9140dbf70e69…` |
| `gemini_raw_baseline` | `gemini-3.5-flash` | locked | `sha256:df5ab24e4c3d…` |

All three share `input_bundle_hash sha256:f29e8a46…` (same frozen bundle as Assembly + the 5
naive baselines). Each produced a valid four-bucket AMFB-v1 prediction summing to 100.

## Adapter fix applied this run
The first attempt returned `schema_failure` for GPT and Claude because the point-in-time
adapters sent parameters the 2026 models reject (HTTP 400, **$0 billed**): `gpt-5.5` requires
`max_completion_tokens` (not `max_tokens`) and rejects a custom `temperature`; `claude-opus-4-8`
deprecated `temperature`. The adapters were updated (OpenAI → `max_completion_tokens`, no
`temperature`; Anthropic → no `temperature`; Gemini unchanged), the two erroneous `schema_failure`
records were discarded (uncommitted), and GPT + Claude were re-run and locked successfully.

## Cost
These SDKs do not return a per-call cost, so each record's `cost_usd` is `0.0` with
`cost_reporting_status = unavailable_sdk_no_cost`; the cap is enforced by worst-case reservation
(≤ floor(max_total/max_per_provider) providers per invocation). Actual real spend was a handful of
short completions (one per provider), **well within the $6 total / $2 per-provider caps**; the two
first-attempt 400 rejections billed nothing.

## Safety
No Assembly prediction or naive output shown to any baseline; no current-campaign data; no outcome
data; no web/search/tool grounding; records immutable and **not** validation cases (never loaded by
the ledger); Tomo remains `pending` / `observed=null` / `action_signals=[]`; ledger 8/6/2/2; Phase
15E blocked. **Scoring (Assembly vs naive vs these LLM baselines) waits until on/after 2026-06-21.**
