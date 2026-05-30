# Phase 15 — Market Evidence & Calibration Layer: Scope & Architecture (DESIGN ONLY)

> **Status: design / scope note. NO implementation.** No backend, frontend,
> model, or calibration-logic changes. This document defines the
> architecture for turning Assembly from a qualitative reaction simulator
> into a **calibrated market-proportion forecaster** with confidence ranges.
> It authorizes design discussion only — see §11 for the staged plan that
> *future* phases would implement.

Related:
- [PHASE_15_MARKET_CALIBRATION_LAYER_PLAN.md](PHASE_15_MARKET_CALIBRATION_LAYER_PLAN.md)
  — the original north-star gate note (do-not-implement-until-14B-ships).
  This scope doc supersedes it in *detail* but inherits its gate and
  anti-overfit law.
- [PHASE_13E_BEHAVIORAL_SHADOW_MODE_DESIGN.md](PHASE_13E_BEHAVIORAL_SHADOW_MODE_DESIGN.md)
  — the behavioral layer is **Tier-4** synthetic simulation here and must
  never gate Phase 15.

---

## 0. North star

Assembly must become the best calibrated market-reaction forecasting engine
in the world — better than Aaru, Simile, Societies, generic AI surveys, and
synthetic focus groups. The moat is **not** smarter-sounding agents. It is
**accurate market proportions** — buyer/action-positive, receptive,
uncertain/proof-needed, skeptical/resistant — **with confidence intervals**,
grounded in public evidence, action proxies, source bias, category priors,
hidden-market estimation, private traction, and (only if validated)
behavioral simulation.

---

## 1. Problem statement

- **Public comments ≠ the market.** Comment threads are a biased, vocal,
  performative sub-sample. They over-represent the opinionated and the
  in-category, and under-represent everyone who acted quietly or not at all.
- **Silent buyers/users are missing.** The people who convert (or churn)
  mostly leave no public trace. The gap between "engaged in the discussion"
  and "took an action" is exactly the gap between Assembly's current
  strength and what founders actually need to know.
- **Channels are biased samples, differently.** Hacker News skews technical
  and skeptical; Product Hunt skews enthusiast and novelty-seeking;
  Kickstarter skews early-adopter and risk-tolerant; Reddit varies by
  subreddit; each distorts the observed stance distribution in a
  characteristic direction.
- **Assembly today is strong at objections, weak at proportions.** It
  reasons well about *why* a market pushes back, but its quantitative
  buyer/receptive/uncertain/skeptical split is not yet calibrated to real
  outcomes. Phase 15 fixes the proportions without sacrificing the
  qualitative strength.

**Design implication:** treat every observed signal as drawn through a
biased lens, estimate and correct the lens, model the unobserved
(hidden-market) population, and report calibrated proportions **with
uncertainty** — never raw comment proportions presented as the market.

## 2. Evidence tiers

Signals are ranked by how predictive they are of real adoption proportions.
Higher tiers carry more weight in the calibrated forecast.

**Tier 1 — real action data (highest weight).** Behavior, not opinion:
purchases, crowdfunding backers, signups, waitlist→paid conversions, demo
requests, installs/downloads, retention/churn, GitHub stars/forks (for dev
tools), app-store installs/reviews. These are the closest proxy to "how
many" and anchor the forecast when present.

**Tier 2 — semi-action data (moderate weight).** Low-commitment engagement:
upvotes, likes, followers, bookmarks, shares, traffic estimates, search
interest (trends), Discord/community joins. Indicates interest magnitude
but not conversion; useful for sizing the receptive/curious band.

**Tier 3 — public opinion data (qualitative weight).** What Assembly is
strong at today: HN/Reddit/Product Hunt/YouTube comments, reviews,
tweets/forum posts. Excellent for *why* (objections, proof needs, language),
weak for *how many*. Drives the qualitative report; contributes to
proportions only after source-bias + action-signal correction.

**Tier 4 — synthetic simulation (model weight).** Assembly's own engine:
the deep-agent debate, the 100-voter influence overlay, and — **only if it
passes net-new validation** ([Phase 13E](PHASE_13E_BEHAVIORAL_SHADOW_MODE_DESIGN.md))
— the behavioral layer. Synthetic signal fills gaps where real/observed
data is thin, and is itself calibrated against Tiers 1–3.

## 3. Source-bias profiles

Each ingestion source carries a measurable, characteristic skew. A
**source-bias profile** is an empirically-estimated transform that maps a
source's observed stance distribution toward the calibrated estimate.
Profiles are **learned from the validation ledger (§9)**, never asserted
from intuition, and each carries a reliability weight + an action-signal
density. Initial profiles to define:

| Source | Characteristic skew (to be calibrated, not assumed) |
|---|---|
| Hacker News | technical, skepticism-heavy, proof-demanding, low-flattery |
| Product Hunt | enthusiast, novelty-seeking, social-proof-sensitive, positivity-biased |
| Kickstarter | early-adopter, risk-tolerant, backing-intent ≠ long-term retention |
| Reddit | high variance by subreddit; ranges critical→advocate |
| GitHub | dev-tool adoption signal (stars/forks); usage ≠ revenue |
| App Store | consumer install/review signal; review skew (extremes over-posted) |
| B2B buyer market | mostly offline/private; public signal severely under-samples it |
| Founder/investor audience | hype-aware, pattern-matching, not the end customer |

**Design implication:** a run drawing from a single skewed source must
surface that limitation in the report's confidence band — a calibrated
output is only as trustworthy as its source mix.

## 4. Action-signal weighting

Core principle: **comments are strong for "why," weak for "how many."**
Action data is strong for "how many." The calibrated forecast weights
expressed signal by its **action tier**, not by volume:

```
committed action (purchased / backed / migrated)
  > intent-to-act (signed up / will buy / requested demo)
    > engaged interest (starred / saved / followed / asked a question)
      > opinion-only (praise / critique, no action)
        > pure affect (emoji, "cool")
```

A single "just bought it / switching our team" datum outweighs many
"looks interesting" comments when estimating proportions. The qualitative
objection/proof extraction is unaffected — action weighting changes the
**proportions**, not the texture. Weights are derived from repeated patterns
across the validation ledger, never hand-tuned to one product.

## 5. Category priors

Different categories have structurally different baseline reaction
distributions and plausible-objection sets. A **category prior** is a
Bayesian starting distribution over buckets (and over which objections are
credible), conditioned on product category, that the run's observed evidence
then updates. Starting categories to define priors for:

- AI note apps / PKM (personal knowledge management)
- developer tools
- open-source software
- B2B SaaS
- consumer apps
- crowdfunding hardware
- AI agents / tools
- productivity tools

Priors are estimated from the validation ledger, labeled explicitly as
category-level, and must **generalize within a category** — never encode a
single product. This is the principled home for the ad-hoc category gates
prototyped in Phase 14B (`isLikelySoftwareProduct` / `isLikelyFreeProduct`):
Phase 15 replaces those heuristics with learned category priors.

## 6. Hidden-market model

The decisive, least-biased reality is mostly **invisible** in public text.
The hidden-market model estimates the latent population behind the vocal
one. It must estimate, per run/source/category:

- **Silent buyers** — converted without posting.
- **Silent users** — adopted/used without posting.
- **Lurkers** — read the thread, never engaged.
- **Upvoters who don't comment** — engagement without opinion text.
- **Buyers who never post** — bought, left no review.
- **Enterprise buyers not online** — B2B decisions made off-channel.
- **Churned users who never complain** — left quietly.

Approach (design intent): from Tier-2 engagement ratios (e.g.
upvotes:comments, views:posts) and category/source priors, infer a latent
population multiplier and a latent stance distribution, then reconcile the
vocal distribution with the latent one. The output is a population estimate
larger than the vocal sample, with the buyer/receptive split driven by
**action signals**, not comment counts. Uncertainty in this estimate widens
the confidence band.

## 7. Private data ingestion (later)

The highest-value, least-biased signal is the founder's own traction.
**Optional, founder-supplied, private**, ingested as high-weight anchors
that pull the forecast toward observed reality and tighten confidence:

- waitlist CSV
- surveys
- interview notes
- sales-call notes
- landing-page analytics
- email-campaign stats
- trial data
- Stripe / customer data
- PostHog / Mixpanel exports
- CRM notes

Strict handling (design intent): private, never leaked into public-facing
transcript text, used only to calibrate proportions and to validate the
model. When present, the report distinguishes "forecast anchored on your
real traction" from "forecast from public signal only." This tier is
**out of scope for the first implementation phases** and is listed here for
architectural completeness.

## 8. Calibrated forecast pipeline

The calibration layer sits **between** the raw simulation output and the
founder-facing report. Each stage is inspectable and recorded for audit:

```
raw deep-agent debate
  → 100-voter influence layer
  → public-evidence model            (Tier 3 extraction)
  → action-signal model              (§4 reweighting by action tier)
  → source-bias adjustment           (§3 per-source transforms)
  → category-prior adjustment        (§5 Bayesian update)
  → hidden-market adjustment         (§6 latent population)
  → private-data adjustment          (§7, only when founder supplies it)
  → final calibrated proportions  +  confidence intervals
```

Design principles:
- **Separation of concerns.** The debate/simulation engine is NOT tuned to
  hit numbers; calibration is a distinct, flag-gated, inspectable layer
  applied afterward, so qualitative reasoning and quantitative calibration
  evolve independently.
- **Auditable.** Every adjustment (which prior, which source profile, which
  action weights, whether private traction anchored it) is recorded, so a
  number can be explained, not just asserted.
- **Confidence intervals are mandatory.** A point estimate without a band is
  overclaiming. Band width is driven by evidence quantity, source diversity,
  prior strength, and whether private traction anchored the estimate. "We
  don't have enough signal to be precise here" is a feature — it is the
  honesty that differentiates Assembly from tools that fabricate precision.
- **Reversible.** The layer ships behind a flag and can be turned off to
  recover the raw distribution, so a calibration regression never silently
  corrupts qualitative output.

## 9. Validation dataset plan

**This is the gate. No calibration ships before it exists.** Calibration
without held-out ground truth is indistinguishable from overfitting.

Requirements:
- **20+ blind launches**, each with: the public signal available at launch
  (the inputs Assembly would ingest), and the **real post-launch observed
  proportions** (Tier-1 outcomes), plus category / source-mix / pricing
  labels.
- **Locked predictions.** Assembly's forecast is recorded *before* the
  outcome is known (or before the modeler sees it), to prevent leakage.
- **Metrics, pre-registered.** avg MAE, TVD (total variation distance), and
  **max single-bucket error** on bucket proportions; objection-overlap
  (did we name the real objections); plus **source-specific failure
  analysis** (where does each source profile break down).
- **Diversity over count.** The 20+ must span categories, sources, and price
  models. Twenty dev tools is not a validation set.
- **Train / held-out split.** Profiles and priors are estimated on a
  training portion; calibration quality is reported on the **held-out**
  portion only.

The six current Phase-12 cases (Tiiny, HasData, Naptick, DocuSeal, Files.md,
Opslane) are the seed; the ledger must grow to 20+ diverse blind cases.
This same ledger is what a future Phase 13 promotion test (§Phase 13E) would
reuse — build it once.

## 10. Anti-overfit rule (hard law)

> **No calibration change unless the pattern repeats across multiple
> validation cases.** Optimize for generalizable market fidelity, never
> benchmark performance on one product.

- No one-product tuning. No product-name conditions / hacks.
- No calibration adjustment unless a repeated failure pattern across
  multiple held-out cases supports it — or it is explicitly labeled
  product/category/source-specific and scoped accordingly.
- Every adjustment is tested on **holdout** cases before acceptance.
- No outcome data (observed proportions, labels, case scores) is ever used
  as a model *input* — only as held-out validation.
- When uncertain, the report widens the interval rather than inventing
  precision.

## 11. Implementation phases (future — not started)

Staged so each phase produces an inspectable artifact and is independently
reviewable. **None are implemented in this doc.**

- **Phase 15A — Evidence schema + source-profile definitions.** Define the
  data model for tiered evidence and the per-source profile schema (skew,
  reliability weight, action-signal density). Artifact: schema + seed
  profiles (uncalibrated placeholders).
- **Phase 15B — Validation-case ledger format.** Define the blind-launch
  ledger schema (inputs, locked prediction, observed outcome, labels, split
  flag) and seed it with the six existing cases. Artifact: ledger spec +
  seed entries.
- **Phase 15C — Action-signal weighting rules.** Define the action-tier
  taxonomy and the reweighting transform (§4). Artifact: weighting spec,
  tested on the ledger.
- **Phase 15D — Source / category priors.** Estimate per-source profiles and
  per-category priors from the training split. Artifact: calibrated profiles
  + priors with held-out error reported.
- **Phase 15E — Calibrated forecast prototype.** Assemble the §8 pipeline
  behind a flag, with confidence intervals + full audit trail. Artifact:
  off-by-default prototype, raw output recoverable.
- **Phase 15F — Compare to baseline on the validation set.** Score the
  prototype against the uncalibrated baseline on the **held-out** ledger
  using the §9 pre-registered metrics. Only a held-out win authorizes
  surfacing calibrated numbers in the founder-facing report.

---

## Sequencing & priority

1. **Phase 14C** (durable artifacts) lands first when approved — without it,
   completed runs (and thus the validation ledger's recorded predictions)
   are not durable.
2. **Phase 15B (validation ledger)** is the highest-leverage first build —
   nothing else can be calibrated or validated without it.
3. Then 15A → 15C → 15D → 15E → 15F in order, each gated on held-out
   performance and the §10 anti-overfit law.
4. The behavioral layer (Tier 4) re-enters only via shadow mode, only after
   it earns promotion on this same ledger ([Phase 13E](PHASE_13E_BEHAVIORAL_SHADOW_MODE_DESIGN.md)).

---

*Design / scope note only. Authorizes no code, schema, model, or
calibration change. Implementation begins, phase by phase, only after
explicit approval and only behind the validation ledger + anti-overfit law.*
