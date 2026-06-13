# Phase 17A — Assembly Market Fidelity Benchmark Spec

**Status:** SPEC / DESIGN ONLY. **No** benchmark is run here. **No** paid Assembly predictions, **no**
paid survey panels, **no** new locks, **no** Tomo scoring, **no** outcomes, **no** model/forecast/prompt
change, **no** calibration, **no** Phase 13 / token change, **no** accuracy claim. This document defines
*how* a future benchmark will measure Assembly's predictive market fidelity against other methods. The
competitor facts below were gathered by a read-only web research pass (June 2026) and are **point-in-time
— re-verify model IDs, tools, and pricing at run time.**

---

## SECTION 1 — Benchmark name and principle

**Name:** **Assembly Market Fidelity Benchmark v1 (AMFB-v1).**

**Core principle:** *Same evidence, same timestamp, same schema, locked before outcome, scored after
outcome.* Every method (Assembly and every baseline) receives an **identical frozen evidence pack** as of
a lock date, emits a prediction in **one canonical schema**, that prediction is **cryptographically
hash-locked before the outcome is knowable**, and all methods are scored against the **same realized,
revealed outcome** with **pre-registered** scoring rules.

**Core question:** *Which method best predicts real future market outcomes from the same pre-outcome
evidence?* The benchmark measures **predictive market fidelity, not report prettiness** — primary ranking
is by **strictly proper scoring rules**, so a method's incentive-optimal strategy is to report its true
belief.

---

## SECTION 2 — Competitor classes

### A. Assembly
- Current Assembly run output (the 4-bucket forecast), mapped into the §3 schema.
- The frozen **evidence snapshot** (`evidence_snapshot_id` + hash) used for the run.
- The **prediction lock** (`prediction_hash`, locked_at, brief_hash) — already Assembly-native (Phase
  14C/15I); it self-reproduces.
- **Cost** (USD per case, from the cost-guard ledger) and **provenance** (run artifacts).
- *Assembly is the system under test; it gets no special advantage beyond format normalization.*

### B. Plain LLM baselines  *(verified June 2026 — re-confirm model IDs at run time)*
Same input evidence pack as Assembly; output forced into the §3 schema; output hash-locked before the
outcome. No hidden extra browsing unless a `search_assisted` benchmark mode is explicitly declared and
its source timestamps are recorded.

| Baseline | Exact model id (snapshot) | Forced-schema method | Notes |
|---|---|---|---|
| GPT-5.5 | `gpt-5.5` (`gpt-5.5-2026-04-23`) | native `response_format: json_schema, strict:true` | OpenAI Responses API; hosted `web_search` for the search-assisted variant |
| GPT-5.5 Pro | `gpt-5.5-pro` (`...-2026-04-23`) | native json_schema strict | higher-reasoning, **costly** ($30/$180 per 1M) — small high-quality baseline only |
| Claude Fable 5 | `claude-fable-5` | **tool_use** (forced `emit_forecast` tool) | GA 2026-06-09; **can refuse** (`stop_reason:"refusal"`) → harness must fall back |
| Claude Opus 4.8 | `claude-opus-4-8` | tool_use (forced tool) | GA 2026-05-28; no refusal path → simpler workhorse Claude baseline |
| Gemini 3.5 Flash | `gemini-3.5-flash` | native `responseSchema` | GA 2026-05-19; can **combine Search grounding + structured output** in one call |
| Gemini 3.1 Pro | `gemini-3.1-pro` (preview) | native `responseSchema` | largest context (2M); use until 3.5 Pro is GA |
| *(pending)* Gemini 3.5 Pro | `gemini-3.5-pro` (**not GA as of 2026-06-12**) | native | announced I/O '26, still Vertex preview — **do not assume availability** |
| **Naive LLM ensemble** | pool of `{gpt-5.5, gpt-5.5-pro, claude-fable-5, claude-opus-4-8, gemini-3.5-flash}` | per-member schema, then **mean/median-pool + renormalize** | "wisdom-of-models" baseline; report alongside single-model baselines |

**Rules:** record exact model name + snapshot string + decoding params (temperature/seed); identical
evidence pack; output validated against the schema (renormalize-or-retry); each locked output hashed.

### C. AI startup-validation tools  *(all currently FAIL the numeric-schema requirement)*
Examples found publicly usable (June 2026): **ValidatorAI**, **DimeADozen.ai**, **IdeaProof**, **Preuve
AI**, **WorthBuild**, **VenturusAI**, **Foundra (AI co-founder)**, **Trend Seeker**, **FounderPal Idea
Validator**, **OpinionX**, **ProductGapHunt**, **ValidateMySaaS**. (*"GoodIdea" could not be verified —
exclude until confirmed.*)

**Finding:** **none** of these emit a calibrated 4-bucket market-reaction distribution — they produce
qualitative reports / single "viability" scores / market-size estimates. Therefore:
- For the distributional benchmark, mark **`schema_failure = true`** for every such tool.
- Score their **qualitative usefulness separately** (the §5 blinded reason rubric: adoption drivers,
  objections, "one thing needed", segment) — they are *advisory* baselines, not distributional ones.
- Record cost, plan, date, and the raw export/screenshot. **No manual interpretation beyond a
  pre-declared, mechanical mapping** (and if no faithful mapping to 4 buckets exists, it stays
  `schema_failure`, not hand-fitted).

### D. Survey / concept-testing platforms  *(empirical-data baselines, NOT same-mechanism competitors)*
These collect human responses; they are an **empirical baseline**, not a forecasting engine like
Assembly. Candidates (June 2026):

| Platform | Panel | Cost (indicative) | Turnaround | Measures |
|---|---|---|---|---|
| SurveyMonkey Audience (Momentive) | built-in | from ~$1+/response | hours–days | **stated preference** |
| Qualtrics (Strategic Research / CoreXM) | BYO or marketplace | enterprise quote | days | **stated preference** |
| Maze (concept/prototype testing) | built-in panel + BYO | SaaS (free + paid tiers) | hours | stated; *some in-prototype behavior* |
| Pollfish (Prodege) | built-in (organic) | from ~$0.95/response | hours | **stated preference** |
| Typeform + paid panel (e.g. Eureka) | BYO + panel add-on | ~$29/mo + panel | hours–days | **stated preference** |
| **Prolific** | built-in research panel | pay-per-reward | hours | **both** — supports real-stakes/behavioral tasks |

**Per-option definitions to record:** survey instrument template (§8), audience size, targeting method,
cost, response time, **question→bucket mapping** (§8), sample quality, denominator quality, and **whether
it predicts future action or only stated preference**.

**Critical distinction (stated vs revealed) — see §7 + §8:** all six are **stated-preference** by default;
Assembly predicts **revealed action**. Raw stated purchase-intent **overstates** real behavior
(hypothetical bias; the intention→behavior "say-do" gap; **self-generated validity** — Chandon, Morwitz &
Reinartz: merely asking intent inflated the intent–purchase correlation by ~58% and its profit-predictive
value by ~120%). So a survey's stated numbers are **not** ground truth and **not** an apples-to-apples
competitor. Use the survey **fairly** by (a) locking its forecast pre-outcome and scoring it against the
**same revealed outcome** Assembly is scored against, and/or (b) building an **incentive-compatible /
real-stakes** instrument (real-money choice, BDM auction, pre-order/commitment, click-to-checkout) — among
these, **Prolific** (and partly Maze's in-prototype behavior) best supports a revealed-action design.

### E. Human expert panel
Founders, product managers, investors, growth marketers, domain experts. **Rules:** blind to outcome;
same evidence pack; same §3 schema; record each panelist's confidence + written reasoning; aggregate
(mean/median per bucket) for a "panel" entry and also keep individuals for variance analysis.

### F. Naive / statistical baselines  *(formulaic — the bar every method must beat)*
- **Always 0% buyer-action** (the maximally-conservative null; note Assembly's two locks currently sit
  here — a key reason these baselines matter).
- **Uniform 25%/bucket** (max-entropy reference).
- **Category average** (mean realized bucket shares for the product category, once ≥k cases exist).
- **Current-traction extrapolation** (project the bucket mix from pre-lock traction).
- **Pledge/day extrapolation** for crowdfunding (linear/decay projection of the funding curve to close).
- **Majority-receptive baseline** (all mass on the modal bucket).
- **Historical source-type baseline** (mean realized shares for the source type — HN/PH/KS/etc. — when
  enough data exists).
Report every method's **skill score** *relative to these* (esp. uniform-25 and base-rate), so "better
than chance" is demonstrated, not assumed.

---

## SECTION 3 — Required prediction schema

One canonical object for **every** method (a method that cannot produce it sets `schema_failure=true`):

```json
{
  "buyer_action_positive": 0.0,
  "receptive": 0.0,
  "uncertain_proof_needed": 0.0,
  "skeptical_resistant": 0.0,
  "confidence": 0.0,
  "top_adoption_reasons": [],
  "top_rejection_reasons": [],
  "one_thing_needed": "",
  "recommended_segment": "",
  "expected_action_signal": "",
  "forecast_notes": "",
  "schema_failure": false
}
```

**Rules:** the four buckets are shares that **must sum to 100** (±a fixed tolerance) **unless**
`schema_failure=true`; `confidence` is explicit and required; **no post-outcome editing**; the output is
**hash-locked** (canonicalize via sorted-key JSON / RFC 8785 JCS + fixed float formatting, then SHA-256
over the *input bundle* `{prompt + frozen evidence pack + model_id/snapshot + decoding params/seed}` **and**
the canonical prediction). A normative draft lives in `docs/PHASE_17A_BENCHMARK_SCHEMA_DRAFT.json`. (For
strictly-proper scoring the harness internally also normalizes the four buckets to a probability
distribution summing to 1.)

---

## SECTION 4 — Case types

| # | Case type | Examples | Scoring (see §5) |
|---|---|---|---|
| 1 | **Full observed distribution** | State-of-CSS-type surveys, public polls with full response distributions | MAE(pp), TVD, **CRPS/RPS** (ordered buckets), ECE/calibration, reason match |
| 2 | **Buyer/action anchor** | Kickstarter, pre-orders, waitlists, signups | **directional hit/miss**, **Brier** (binary "material action?" prob), log error on volume if predicted, action-signal tier |
| 3 | **Conversion** | exposed audience → signup/preorder conversion | conversion-rate error, directionality, calibration |
| 4 | **Ranking / leaderboard** | Product Hunt monthly leaderboard | **Spearman** rank correlation, **top-k hit rate** |
| 5 | **Survey-baseline** | human survey on the concept | later-outcome prediction; bucket-distribution match if a real distribution resolves; explicit **stated-vs-revealed gap** measurement |

> Buyer/action-anchor cases (types 2) are **partial** — they score only the buyer direction (the Phase
> 16B-R `action_anchor_only` convention) and never yield a four-bucket MAE. Only **type 1** (and resolved
> type 5) give a full distribution.

---

## SECTION 5 — Metrics

**Primary ranking = strictly proper scoring rules** (so honest reporting is optimal). Distances
(MAE/RMSE/TVD) are **secondary/descriptive**.

**Full distributions**
- **Proper (primary):** **Brier** (multiclass quadratic), **Log score** (with a *pre-registered* epsilon
  floor so zeros don't send it to ∞), **CRPS/RPS** (respects the *ordered* receptivity buckets — "one
  bucket off" beats "three off").
- **Descriptive (secondary):** MAE in percentage points (L1), RMSE on bucket shares, **Total Variation
  Distance**, KL / Jensen-Shannon divergence (with the same epsilon convention).
- **Calibration:** reliability diagram, **ECE** (pre-registered bin count/scheme) + a binning-robust
  companion (**Adaptive ECE**), MCE, **calibration-in-the-large** (intercept) and **calibration slope**;
  confidence-interval **coverage**. Evaluate **sharpness *subject to* calibration** (Gneiting paradigm) —
  never one in isolation.

**Buyer/action**
- Directional accuracy; **Brier score** on the "material buyer/action?" probability; precision/recall on
  buyer-action; action-signal classification (Tier 1/2); **missed-positive severity** (how badly a
  0%-buyer prediction missed a strongly-funded campaign — the Hollowed Oath failure mode).

**Rankings**
- **Spearman** rank correlation; **top-k hit rate**.

**Qualitative reasons (blinded human rubric)**
- adoption-driver match; objection match; "one thing needed" accuracy; segment-recommendation accuracy.
  Scored by a rater **blind** to which method produced which output.

**Operations**
- cost per case; latency; reproducibility; **variance across repeated runs** (re-run each stochastic
  method N times); auditability/provenance; **schema-failure rate**.

**Skill scores:** report each metric **relative to the naive baselines** (esp. uniform-25 and base-rate),
with **bootstrap confidence intervals** and **paired significance tests** on per-item score differences.

---

## SECTION 6 — Fairness rules

1. **Same frozen evidence snapshot** for every method (hash/snapshot the inputs; no method sees what
   others can't).
2. **Same timestamp** / lock date; **same outcome deadline**; score only outcomes resolving **after** the
   lock.
3. **Same §3 schema**; **same no-leakage rule** (§7); **same cost recording**.
4. **No outcome contamination** of any prompt, context, training corpus, or tuning loop before lock.
5. **No model-specific prompt advantage** beyond format normalization (identical task framing; only the
   schema-forcing mechanics differ by provider).
6. **All predictions immutable after lock** (hash-committed).
7. **Identical preprocessing/smoothing** (flooring/normalization/epsilon) fixed in advance and applied
   symmetrically to every method.
8. **Pre-registration** of the full analysis plan before any outcome is seen (§9).
9. **Pre-specified, blinded resolution criteria** — outcomes adjudicated blind to which method produced
   which forecast where possible.
10. **Equal evidence & comparable effort/compute** — never attribute a gap to method quality when it's an
    information/effort asymmetry.

---

## SECTION 7 — Leakage rules (forbidden)

- ❌ Using **post-outcome** sources or final stats in any prompt/evidence pack.
- ❌ Using **live web after lock** unless every source's timestamp is recorded and predates the lock
  (search-assisted mode must log source dates).
- ❌ Comparing Assembly's **locked pre-outcome** prediction against a competitor's **post-outcome** guess.
- ❌ Any **manual edit** after the prediction is locked.
- ❌ **Hand-mapping** qualitative output into favorable buckets without pre-declared, mechanical rules.
- ❌ **Look-ahead / test-set contamination** (future values in features; outcomes in an LLM's training or
  context).
- ❌ Scoring against **stated intentions** instead of **revealed behavior** (rewards the say-do gap).

---

## SECTION 8 — Survey instrument design

**Standard concept-test question (maps 1:1 to Assembly's buckets):**

> *After seeing this product concept, which best describes you?*
> **A.** I would buy / back / sign up **now**. → **buyer_action_positive**
> **B.** I'm interested and would consider it. → **receptive**
> **C.** I need more proof, reviews, price clarity, or trust before deciding. → **uncertain_proof_needed**
> **D.** I would not use or buy this. → **skeptical_resistant**

**Also collect:** willingness-to-pay; confidence; top objection; top appeal; demographic/domain relevance;
prior category familiarity. **To narrow the stated-vs-revealed gap** (§2D/§7): deflate top-2-box with a
known intent→behavior calibration (Juster scale / top-2-box discount) **and/or** add an
**incentive-compatible** element (real-money choice, BDM auction, pre-order/commitment, click-to-checkout)
— best supported on Prolific. The survey forecast is **locked pre-outcome** and scored against the **same
revealed outcome** as Assembly (never survey the same people whose later behavior is the scored outcome —
self-generated validity).

**Survey sample sizes:** smoke test **n=50**; internal benchmark **n=100**; stronger benchmark **n=300+**;
public claim **larger and source-diverse**.

---

## SECTION 9 — Minimum sample sizes for benchmark claims

| Tier | Gate |
|---|---|
| **Internal exploration** | ≥ **10** prospective cases |
| **Credible internal benchmark** | **25–30** prospective cases · ≥ **3** source categories · ≥ **5** direct/full-distribution cases if possible |
| **Public marketing claim** | **50+** prospective cases · multiple source types · **locked competitor baselines** · **pre-registered** scoring rules · **no cherry-picking** · **confidence intervals** reported |

Proper-score differences are **noisy at small n** (for scale: Good Judgment's headline Brier gaps came
from ~500 questions / >1M judgments) — never declare a winner without CIs + paired tests.

---

## SECTION 10 — Leaderboard design (future)

Columns: **method** · cases scored · **buyer/action directional accuracy** · **full-distribution MAE** ·
**TVD** · **calibration error (ECE/ACE)** · **proper score (Brier/log/CRPS)** · **reason-match score** ·
**cost/case** · **latency** · **auditability score** · **schema-failure rate**. Every cell carries a
**bootstrap CI**; ranking is by the **proper score**, with distances shown as descriptive context.

---

## SECTION 11 — Phase roadmap

- **Phase 17B —** Benchmark harness for **plain LLM + naive baselines** (schema-forcing per provider,
  canonicalize + hash-lock, the naive/statistical baselines, the metrics library). *Code, but no live
  paid runs without explicit approval + cost gate.*
- **Phase 17C —** **Survey instrument + human-panel template** (the §8 instrument, the bucket mapping, the
  incentive-compatible variant, the panel protocol).
- **Phase 17D —** **Competitor-tool capture protocol** (how to run + export the §2C tools, the
  schema_failure handling, the qualitative rubric).
- **Phase 17E —** **First prospective benchmark batch** (locked, pre-registered; under cost approval).
- **Phase 17F —** **Benchmark leaderboard / report** (scoring + CIs + skill scores).

---

## SECTION 12 — Explicit non-claims

- Assembly is **not benchmark-proven** yet.
- **One Hollowed Oath miss is not enough**; it is a single partial buyer-anchor data point.
- **Tomo is pending** (score on/after 2026-06-21).
- **No public accuracy claim** is made or implied by this spec.
- **No calibration** is derived from designing this benchmark.
- **This benchmark spec does not change the model** — it defines measurement only.

---

## Appendix — Output locking (mechanics)

1. **Fix the schema** (strict; `additionalProperties:false`; all fields required; buckets sum to 100/1).
   OpenAI: `response_format:{type:'json_schema', strict:true}`. Gemini: `responseSchema` (works with
   Search grounding). Claude: a forced `emit_forecast` tool whose `input_schema` is the object
   (`tool_choice`), validate the returned input, **handle Fable 5 refusals** with fallback.
2. **Canonicalize** (RFC 8785 JCS or sorted keys + fixed float formatting; UTF-8; no insignificant
   whitespace) so the same logical object → identical bytes.
3. **Hash-lock** (commit-then-reveal): SHA-256 (or BLAKE3) over the canonical bytes of **both** the input
   bundle (prompt + frozen evidence pack + model_id/snapshot + decoding params/seed) **and** the
   prediction. Store the digest before the outcome; reveal + verify after. (Mirrors Assembly's existing
   `prediction_hash` self-reproduction discipline.)

*All competitor facts are June-2026 point-in-time and must be re-verified at run time.*
