# Phase 17B-L — Tomo Baseline Lock Preflight + Approval-Gated Live Lock

**Status:** baseline infrastructure + naive locks complete; paid LLM baselines **PREPARED, NOT RUN**.
**No** Tomo scoring, **no** outcome added, **no** current campaign/web data, **no** paid provider call,
**no** forecast/calibration/prompt/Phase-13/token change, **no** accuracy claim.

## Why this phase exists

Tomo: Endless Blue is a **perishable** prospective benchmark. Assembly locked its prediction on
2026-06-04 (before the outcome). If competitor baselines (GPT / Claude / Gemini + naive) are not locked
**on the same frozen evidence before the outcome date (2026-06-21)**, they become contaminated — a model
asked after the campaign closes can simply recall what happened. This phase preserves the fair comparison
by freezing the shared input and locking everything that can be locked for free now, while gating the paid
calls behind explicit approval.

## The same-frozen-evidence rule (the core fairness invariant)

Every benchmarked method must receive the **identical** frozen input bundle Assembly used — and nothing
else. The bundle lives at
`apps/api/benchmarks/market_fidelity/prospective_baseline_inputs/tomo_endless_blue_2026/input_bundle.json`
and is bound to a single `input_bundle_hash` (`sha256:f29e8a46…`) that **every** locked record carries.

**Authenticity.** `input_bundle.json` embeds Assembly's founder-brief **product fields verbatim**:
`sha256(json.dumps(brief, sort_keys=True, separators=(',',':')))` reproduces Assembly's git-committed
`brief_hash sha256:4b188a0d…` (run `4fcc4cbf…`, `prediction_hash sha256:0a9ce639…`). So the product content
is the *same* input — not a reconstruction from the current web. The one field that is **not** verbatim is
`campaign_context`: its model-facing text is a **neutral rewrite** that strips the original brief's
benchmark/orchestration meta (the "Assembly" name, the Phase-16A framing, the live-status / in-progress-tally
references, and the scoring-mechanism sentence) so the prompt carries no biasing commentary; the original
verbatim `optional_context` is preserved in `provenance.json` for audit.

## Why current web / current campaign data are banned

A prospective benchmark is only fair if all methods predict from the **same pre-outcome** information. The
model-facing bundle therefore **excludes**: the current/in-progress funding amount, the current backer
count, live comments, any post-lock press, the final outcome, **Assembly's own prediction**, and Assembly's
144-item internal evidence-retrieval content (Assembly-internal; kept out so the comparison is brief-vs-brief
and provably leakage-free). The leakage guard (`validators.check_no_post_lock_sources`) confirms every named
source's `retrieved_at` predates the 2026-06-04 lock; the bundle was found **clean**.

## What was locked (now)

Naive baselines (deterministic, zero-cost, `mode=naive`, `observed=null`, same `input_bundle_hash`,
immutable) under `apps/api/benchmarks/market_fidelity/baseline_predictions/`:

| method | result on Tomo's bundle |
|---|---|
| `naive_always_zero_buyer` | 0 / 33.3 / 33.3 / 33.3 |
| `naive_majority_receptive` | 0 / 100 / 0 / 0 |
| `naive_uniform_distribution` | 25 / 25 / 25 / 25 |
| `naive_category_prior_placeholder` | **schema_failure** (no direct-observed category prior exists — honest) |
| `naive_crowdfunding_goal_progress_placeholder` | **schema_failure** (no pre-lock progress fields in the bundle — by design) |

The two `schema_failure` records are the **honest** outcome: those baselines require inputs the bundle
deliberately does not contain (a category prior; the in-progress funding tally). Including those inputs
would be leakage, so the baselines correctly decline rather than invent a distribution.

## What was prepared but NOT run

`gpt_raw_baseline` / `claude_raw_baseline` / `gemini_raw_baseline` (`method_class=plain_llm`):
**PREPARED_NOT_RUN.** No paid call was made; no API key was read; no SDK is imported. The preflight
(`scripts/phase_17b_l_baseline_preflight.py`) prints the input + prompt hashes, the would-be output paths,
the point-in-time model ids (**re-verify at run time**), and the exact later command.

### The single strict baseline prompt

`baseline_prompt.build_baseline_prompt` renders ONE prompt (same for every provider; `prompt_hash`
recorded) that: gives only the frozen bundle, asks for the canonical AMFB-v1 four-bucket prediction
(+ adoption/rejection reasons, one-thing-needed, segment, expected_action_signal, confidence), forbids
outside knowledge / web / current status, and requires an explicit `schema_failure` if the model cannot
comply. It is rendered only from whitelisted model-facing fields, so it can never contain Assembly's
prediction, the outcome, or the Hollowed Oath case.

## Cost / approval status

No live calls were approved for this run, so none were made. A live lock requires **all** of: the approval
flag (`ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true` or `--i-understand-this-costs-real-money`), a positive
global cost cap, a positive per-provider cost cap (≤ global), and explicit providers. The gate
(`live_call_gate.evaluate_live_call_gate`) is **fail-closed**; even when it approves, this isolated package
never spends — the paid executor is a separate, gate-guarded step.

```bash
# lock paid LLM baselines later (before 2026-06-21), with approval + caps:
ASSEMBLY_ALLOW_LIVE_BASELINE_CALLS=true PYTHONPATH=src .venv/bin/python \
  scripts/phase_17b_l_baseline_preflight.py \
  --input-bundle benchmarks/market_fidelity/prospective_baseline_inputs/tomo_endless_blue_2026/input_bundle.json \
  --providers openai anthropic google --max-total-usd 6 --max-per-provider-usd 2 \
  --i-understand-this-costs-real-money
```

## Hardening (6-agent adversarial review)

`spend_safety`, `isolation_ledger`, and `docs_noclaim` held with zero findings. The rest were fixed:
- **campaign_context** stripped of benchmark/Assembly/live-status meta (above); `assembly` and `phase 16a`
  added to the prompt-cleanliness leak anchors.
- **`BaselinePredictionRecord` is now self-verifying** — a `@model_validator` recomputes `prediction_hash`
  and rejects any record whose stored hash does not reproduce or whose `schema_failure` flag disagrees with
  its payload, so a hand-edited on-disk record can never load.
- **The preflight is now operationally as strong as the tests** — it derives Assembly's forbidden values
  (prediction hash + locked proportions, from `provenance.json` / the lock record, never displayed) and
  passes them to the cleanliness check, asserts the recomputed `input_bundle_hash` equals the committed
  `input_bundle_hash.txt` (fail-closed on drift), and rejects a non-object bundle cleanly.
- A future gate-guarded **paid executor** must likewise re-derive and assert the shared `input_bundle_hash`
  before writing any provider record.

## No accuracy claim

This phase makes **no** claim that Assembly beats any method. It only freezes a fair, leakage-controlled
comparison so that — **on/after 2026-06-21** — Tomo can be scored once against Assembly + these baselines
(buyer-anchor only, via the Phase 15L-C gated mapping). Tomo's validation case remains `pending`,
`observed=null`, `action_signals=[]`, `prediction_hash` intact, and the ledger stays at 8/6/2/2.
