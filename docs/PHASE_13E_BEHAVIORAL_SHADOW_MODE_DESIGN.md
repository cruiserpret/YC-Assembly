# Phase 13E — Behavioral Agent Mind Layer: Shadow-Mode Design (DESIGN ONLY)

> **Status: design note. NOT implemented. NOT wired. Flags OFF.**
> No production code, no behavioral_mind_layer wiring, no `ASSEMBLY_BEHAVIORAL`
> flag enablement, no paid run. This document freezes the Phase 13B-rev4
> offline result as a *documented lead* and specifies what a future
> shadow-mode would look like **if and only if** it earns promotion on
> net-new validation cases (see §7). It authorizes nothing.

Related: [PHASE_15_MARKET_EVIDENCE_AND_CALIBRATION_SCOPE.md](PHASE_15_MARKET_EVIDENCE_AND_CALIBRATION_SCOPE.md)
— the calibrated-forecast roadmap, which is the **higher-priority** path
to the north star. The behavioral layer is Tier-4 synthetic simulation in
that architecture and must never gate Phase 15.

---

## 1. What Phase 13 attempted

The Behavioral Agent Mind Layer adds a per-persona *decision model* on top
of the existing deep-agent debate + 100-voter overlay. Instead of mapping
a persona's stance text to a market bucket directly, it derives a
`BehavioralVector` (50+ literature-grounded traits — Diffusion of
Innovations, Cialdini, Theory of Planned Behavior, Social Judgment Theory),
then routes each persona through a decision-logic function
(`compute_behavioral_response`) that produces a **sentiment label** + an
**action propensity**, which map to the four market buckets:
buyer/action-positive, receptive, uncertain/proof-needed, skeptical/resistant.

The hypothesis: a behaviorally-grounded action model would produce more
realistic **proportions** than stance-text mapping — Assembly's known weak
spot (strong at *why*, weak at *how many*).

All Phase 13 work to date has been **offline replay diagnostics** against
six locked Phase-12 validation cases (Tiiny, HasData, Naptick, DocuSeal,
Files.md, Opslane), measured by mean absolute error (MAE, in percentage
points) against observed bucket proportions. **No production behavior has
ever changed; flags default to `False`; the code is untracked.**

The bar to beat is the **no-behavioral baseline: avg MAE 25.30 pp** (the
current stance-text mapping). A behavioral variant only earns attention if
it beats that.

## 2. Why rev2 and rev3 failed

| Variant | avg MAE (pp) | vs baseline 25.30 |
|---|---:|---:|
| rev2 (basic derivation + Phase 13C-rev2 routing) | 31.14 | **+5.84 worse** |
| rev3 (13B-rev3 enriched derivation + same routing) | 27.37 | **+2.07 worse** |

- **rev2** failed because the `BehavioralVector` was *input-starved*: only
  ~6 of 50+ trait fields were derived from existing persona signals; the
  rest took neutral `0.5` defaults. That collapsed per-persona variance, so
  the routing logic had almost nothing to differentiate personas on —
  cases collapsed to a single action / single bucket. 4 of 6 cases
  regressed vs baseline.
- **rev3** enriched the derivation (~40 fields from role/stance/rule/text
  heuristics). This was directionally correct — it cut MAE 31.14 → 27.37,
  restored skeptical-bucket survival, and tripled diversity on some cases —
  but it was **still worse than the no-behavioral baseline**, and the
  diagnostic isolated the residual failure as an *action_score → bucket
  band* problem **plus** a deeper *sentiment-classification* problem.

## 3. What rev4 changed

rev4 added a single generic routing branch ("Branch 6.5") to the action
banding: a **trialability / low-friction-adoption receptive route**.

A persona at action_score 0.20–0.59 who is:
- **not strongly rejecting** (sentiment not skeptical/rejecting; low
  rejection score), AND
- has **moderate willingness** to try unproven products, AND
- has **moderate curiosity / novelty fit** (`curiosity_score ≥ 0.45`), AND
- is on a product that is **low-risk to adopt** — proof-satisfied **OR** a
  real low-deposit trial exists **OR** the full product is
  free/open-source/self-serve (`low_friction_adoption`)

…routes to `WOULD_TRY_ONCE` → **receptive**, instead of being stranded in
passive WAIT/uncertain.

It also introduced a generic, pre-outcome product attribute
`low_friction_adoption` (free / open-source / self-serve), set from real
product facts: paid hardware (Tiiny) = False; free-tier and open-source
products = True. **The band threshold *numbers* were not tuned** — rev4 is
an added routing branch, not a weight change.

## 4. Why low-friction adoption / trialability helped

The residual failure rev3 exposed was the **"receptive band-7 gap"**:
proof-satisfied or curious users on free/open-source products clustered at
action_score 0.20–0.40 (the low-action band), where the old logic only
emitted passive "wait/uncertain" — never "try." But the *theory* says the
opposite: per Rogers' Diffusion of Innovations, **trialability** and **low
perceived adoption risk** lower the behavioral action bar. A free,
self-serve, open-source tool costs a curious user almost nothing to *try* —
so a curious fence-sitter on such a product behaves as **receptive**
(will try once), not uncertain.

Encoding that mechanism (low adoption risk + curiosity → try) surfaced the
receptive cohorts that the HN open-source cases actually had (Files.md
observed 19% receptive, Opslane 44%) and that every prior variant predicted
at ~0%.

## 5. rev4 results

Offline replay, **$0, 0 LLM calls, 0 DB/network**. Baseline = 25.30 pp.

| Variant | avg MAE (pp) | vs baseline |
|---|---:|---:|
| no-behavioral **baseline** | **25.30** | — |
| rev3 (enriched + old bands) | 27.37 | +2.07 |
| **rev4 Mode A** (enriched, source priors **ON**) | **24.25** | **−1.05 ✅ beats** |
| **rev4 Mode B** (enriched, source priors **OFF**) | **20.83** | **−4.47 ✅ beats clearly** |

Per-case (Mode A, priors ON): improves **4/6** — DocuSeal −9.87, Files.md
−6.41, HasData −5.40, Naptick −4.04; regresses 2 — Tiiny +12.17 (unchanged
across all revs), Opslane +7.23. In Mode B (priors OFF), Opslane improves
to 2.55 MAE (−12.77) → **5/6 improve**, leaving Tiiny the lone regressor.

**This is the first behavioral variant to beat the no-behavioral baseline.**
A control run (basic derivation + rev4 bands) scored 29.53 — confirming the
win needs **both** the rev3 enrichment **and** the rev4 route together.

## 6. Why it is NOT production-ready

- **Thin production-mode margin.** Production-realistic mode (source priors
  ON) beats baseline by only **1.05 pp**, and Opslane still regresses there.
  The large win (−4.47) requires turning source priors OFF, which is *not*
  how production runs.
- **Strict locked gate still fails.** `overall_pass = False`: no-regression
  fails (Tiiny), no-all-one-bucket fails (Tiiny/HasData/Naptick still
  collapse to one action), receptive-survival fails across all cases
  (HasData/Naptick/Tiiny still emit ~0% receptive).
- **The deeper limiter is upstream.** Diagnostics showed **0/11 (Files.md)
  and 0/10 (Opslane)** personas are classified positive/curious — the model
  labels nearly all HN personas skeptical/uncertain, and the per-persona
  derivation does not reproduce the real bucket mix (Files.md model ≈ 64%
  uncertain vs reality 52% skeptical). The action band was only a
  *secondary* constraint; **sentiment / persona derivation** is the binding
  one. No band tweak fixes mis-derived personas.

## 7. Overfitting / generalization risk (read this before trusting rev4)

rev4's win carries **material overfitting risk** and must be treated as a
*hypothesis*, not a validated result:

1. **Signal + gate were adapted to the data.** The `low_friction_adoption`
   signal was added, and the gate was relaxed from "positive/curious" to
   "not-rejecting + curiosity ≥ 0.45", **after** observing that the first,
   stricter version fired zero times. That is adaptation to the six known
   cases, even though no numeric weight was tuned to MAE.
2. **Wins concentrate in flagged cases.** The improvement is concentrated in
   the three cases manually flagged `low_friction_adoption=True` and is
   **mode-fragile** (Opslane only wins with priors OFF).
3. **N = 6 cannot prove generalization.** A hand-set per-case product
   attribute on six cases tells us nothing about net-new launches.

The `low_friction_adoption` rule is theory-sound and *should* generalize
(free/OSS products genuinely lower trial risk for any audience), but
"should" is not "does." **Only net-new validation resolves this.**

## 8. Why no paid Phase 13E should run yet

A paid Phase 13E (LLM net-new validation cases) is the *only* test that
resolves §7 — but it is premature now because:
- Phase 15 (calibrated evidence layer) is the higher-leverage path to the
  north star and should consume effort first.
- Running paid net-new cases for the behavioral layer **before** the Phase
  15 validation-dataset ledger exists would duplicate work — Phase 15
  builds exactly the 20+ blind-launch ledger that a Phase 13 promotion test
  would need. Build the ledger once, in Phase 15, and reuse it.
- The behavioral layer is Tier-4 synthetic simulation; it is downstream of
  the evidence/source/category calibration that Phase 15 delivers. Calibrate
  the cheaper, higher-signal layers first.

**Decision: park rev4 as a documented lead. Do not spend on Phase 13E
validation until the Phase 15 validation ledger exists.**

## 9. What "shadow mode" would mean later

If and only if rev4 (or a successor) earns promotion on net-new validation
(§10), "shadow mode" is the safe way to introduce it:

- The **current calibrated forecast remains the default** shown to users.
- The behavioral forecast is **computed separately**, in parallel, never
  shown as the final answer.
- The system **logs the delta** (behavioral vs default) per run and per
  bucket, accumulating a comparison record against real outcomes.
- The behavioral forecast is **never exposed as the final forecast** until
  it demonstrably beats the default on the validation ledger over a
  meaningful sample.
- Shadow mode is itself **flag-gated and off by default**; turning it on
  computes + logs only, with zero user-facing change.

Shadow mode is a *measurement* tool, not a release. It is not implemented
here and must not be implemented until §10 is met.

## 10. Pass criteria for any future Phase 13 promotion

A behavioral variant may be promoted out of shadow mode **only if all** hold
on the **net-new validation set** (not the six tuning cases):
- improves net-new validation cases (lower avg MAE/TVD than the default
  calibrated forecast), **and**
- no catastrophic regression on any case, **and**
- improves buyer / action-positive prediction, **and**
- improves uncertain vs skeptical separation, **and**
- improves action-despite-skepticism modeling (curious users on low-risk
  products predicted as receptive, not uncertain), **and**
- no outcome leakage (no observed proportions, labels, or case scores used
  as model input), **and**
- no one-product tuning (no product-name conditions; every adjustment
  validated on holdout cases).

Until then the behavioral layer stays Tier-4, flag-off, shadow-only-when-
earned, and **never** the default forecast.

---

*Design note only. Authorizes no code, no wiring, no flags, no paid run.
The next priority is Phase 15, not Phase 13.*
