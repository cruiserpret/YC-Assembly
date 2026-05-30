# Phase 15 — Market Calibration Layer (DESIGN ONLY)

> **Status: design note. DO NOT IMPLEMENT YET.**
> This document describes a future accuracy roadmap. No code, schema, or
> pipeline change should be built from it until **both** of the following
> are true:
> 1. **Phase 14B is shipped** (100-voter graph + report consistency, the
>    branch `fix/phase-14b-voter-graph-and-report-consistency`, merged and
>    in production), and
> 2. A concrete **validation dataset plan** exists — a named set of real
>    launches with real post-launch outcomes we can score against
>    (see §8). Without that ground truth, "calibration" is just tuning,
>    and tuning without a held-out validation set is overfitting.

---

## 0. Why this document exists

Assembly today is **strong at qualitative reasoning** — realistic
objections, proof needs, the texture of how a market argues with itself.
It is **weak at quantitative proportions** — the exact share of buyers vs.
receptive vs. uncertain vs. skeptical, and how that maps to real-world
adoption.

Phase 15 is the plan to close that quantitative gap **honestly**: to turn
Assembly from a debate-transcript generator into a *calibrated
market-reaction forecasting engine* whose numbers mean something because
they have been checked against reality.

This is the moat. Not "AI agents talking" — **calibrated market-reaction
forecasting**: realistic objections, realistic proof needs, realistic
buyer/receptive/uncertain/skeptical proportions, clear source/context
limits, investor-grade reporting, and validation against real market data.

The North Star: be better than Aaru, Simile, Societies, generic AI
surveys, and synthetic focus groups — measured on **generalizable market
fidelity across products, sources, and categories**, never benchmark
performance on a single product.

---

## 1. Why public comments ≠ market

The raw signal Assembly ingests (and that personas are derived from) is
heavily weighted toward **public, expressed text**: launch-page comments,
forum threads, social replies, review snippets. Public comment is a
biased estimator of the market for structural reasons:

- **Selection bias.** People who comment are not a random sample. They
  skew toward the highly engaged, the highly opinionated, and the
  already-in-category. Quiet satisfied buyers and quiet non-buyers are
  both under-represented.
- **Vocality ≠ intent.** A loud objection and a silent purchase are both
  one data point in real adoption, but public text massively
  over-samples the loud objection.
- **Performativity.** Public comments are written for an audience.
  Skepticism signals sophistication; enthusiasm signals naïveté. The
  medium pushes the expressed distribution toward critique.
- **Platform skew.** A Show HN crowd, a Product Hunt crowd, a Reddit
  subreddit, and a LinkedIn feed each carry a different baseline stance
  toward the same product.

**Design implication:** the model must treat the observed comment
distribution as a *sample drawn through a biased lens*, and explicitly
estimate and correct for that lens — rather than reporting the raw
public-comment proportions as if they were the market.

---

## 2. The hidden-buyers / hidden-users problem

The people who actually convert are largely **invisible in public text**.

- Buyers who silently sign up leave little or no public trace.
- Churned or never-interested users leave even less.
- The gap between "engaged with the discussion" and "took an action" is
  exactly the gap between Assembly's current strength (discussion) and the
  thing founders actually want to know (will they buy / adopt / pay).

**Design implication:** Phase 15 must model a *latent population* larger
than the vocal population, and must make the buyer/non-buyer split a
function of **action signals**, not of comment volume. The 100-voter
overlay shipped in Phase 14A/14B is the surface where a calibrated latent
distribution would eventually be displayed — but the *numbers behind it*
are what Phase 15 makes trustworthy.

---

## 3. Action-signal weighting

Not all expressed signal is equal. A comment that says *"just bought it"*,
*"switching our team next week"*, or *"added to cart"* is worth far more as
a predictor of real adoption than *"looks interesting"* or *"the pricing
seems steep."*

Phase 15 design intent:

- Define an **action-signal taxonomy** ordered by predictive weight, e.g.
  committed action (purchased / migrated) > intent-to-act (will try / will
  buy) > engaged-interest (saved, asked a question) > opinion-only
  (praise / critique with no action) > pure affect (emoji, "cool").
- Weight each expressed datum by its action tier when estimating the
  latent buyer share, instead of counting all stances equally.
- Keep the qualitative objection/proof extraction unchanged — action
  weighting affects the **proportions**, not the texture.

This must be a **generic** weighting scheme derived from repeated patterns
across many launches, not hand-tuned per product (see §9).

---

## 4. Source-bias profiles

Each ingestion source carries a measurable baseline bias. Phase 15 design
intent is to maintain **per-source bias profiles** — empirically estimated
offsets/transforms that map a source's observed stance distribution toward
the calibrated market estimate.

- A profile captures, for a source (Product Hunt, Hacker News, Reddit,
  X/Twitter, App Store reviews, founder's own private list, etc.): its
  typical skew (e.g. HN over-indexes skeptical/technical objections),
  its action-signal density, and its reliability weight.
- Profiles are **learned from the validation dataset** (§8), never
  asserted from intuition.
- A run that draws from a single skewed source must surface that in the
  report's limitations — calibrated output is only as trustworthy as the
  source mix, and the report must say so.

---

## 5. Category priors

Different product categories have structurally different baseline reaction
distributions. A dev tool, a consumer subscription app, a B2B platform,
and a physical good do not convert the same way, and their public
discourse skews differently.

Phase 15 design intent:

- Maintain **category priors** — a prior distribution over
  buyer/receptive/uncertain/skeptical (and over which objections are
  plausible) conditioned on product category.
- Use the prior as the Bayesian starting point that the run's observed
  evidence updates, rather than starting every product from a flat prior.
- Priors are estimated from the validation dataset and explicitly labeled
  as category-level — they must generalize within a category, not encode a
  single product.
- This is the principled home for the kind of category-awareness already
  prototyped narrowly in Phase 14B (`isLikelySoftwareProduct` /
  `isLikelyFreeProduct` gating which objections are plausible): Phase 15
  would replace ad-hoc gates with learned category priors.

---

## 6. Private traction data inputs (founder-supplied ground truth)

The single most valuable, least biased signal is the founder's **own
private traction**: waitlist size, signup conversion, trial-to-paid rate,
early revenue, retention. This data is hidden from public text but often
available to the founder.

Phase 15 design intent:

- Allow founders to **optionally** supply private traction inputs as
  first-class evidence (e.g. "1,200 signups, 4% trial-to-paid, 60% week-4
  retention").
- Treat these as **high-weight anchors** that pull the calibrated forecast
  toward observed reality and tighten confidence (see §7).
- Strict handling: private, never leaked into public-facing transcript
  text, used only to calibrate proportions and to validate the model.
- When present, the report should distinguish "forecast anchored on your
  real traction" from "forecast from public signal only."

---

## 7. The calibrated forecast layer

The calibrated forecast layer is the component that sits **between** the
raw simulation output and the founder-facing report. Conceptually:

```
raw expressed distribution (from debate + voters)
        │
        ├─ action-signal reweighting        (§3)
        ├─ source-bias correction           (§4)
        ├─ category prior update (Bayesian)  (§5)
        ├─ private-traction anchoring        (§6, when supplied)
        ▼
calibrated buyer/receptive/uncertain/skeptical estimate
        + confidence interval               (§7.1)
```

Design principles:

- **Separation of concerns.** The simulation/debate engine stays as-is and
  is *not* tuned to hit numbers. Calibration is a distinct, inspectable
  layer applied afterward, so qualitative reasoning and quantitative
  calibration evolve independently.
- **Auditable.** Every adjustment (which prior, which source profile,
  which action weights, whether traction was supplied) is recorded so a
  report's number can be explained, not just asserted.
- **Reversible / flaggable.** The calibrated layer ships behind a flag and
  can be turned off to recover the raw distribution, so a regression in
  calibration never silently corrupts the qualitative output.

### 7.1 Confidence intervals

A calibrated point estimate without an uncertainty band is still
overclaiming. Phase 15 must report **confidence intervals**, not single
numbers:

- Width driven by evidence quantity, source diversity, category-prior
  strength, and whether private traction anchored the estimate (thin or
  single-source evidence ⇒ wide band; rich, multi-source, traction-anchored
  evidence ⇒ tight band).
- The report states the band prominently and explains *why* it is wide
  when it is wide. "We don't have enough signal to be precise here" is a
  feature, not a failure — it is the honesty that differentiates Assembly
  from tools that fabricate precision.
- Thin-evidence guardrails already in the codebase (pre-launch / thin
  evidence handling) are the natural ancestors of this band logic.

---

## 8. Validation dataset of 20+ launches

**This is the gate. No calibration is built before this exists.**

Calibration without held-out ground truth is indistinguishable from
overfitting. Phase 15 requires a **validation dataset of at least 20 real
launches**, each with:

- The public signal that was available at/around launch (the inputs
  Assembly would have ingested),
- The **real post-launch outcome** (adoption, conversion, revenue, or a
  documented qualitative verdict) — the ground truth,
- Category, source mix, and pricing model labels so per-category and
  per-source profiles can be estimated and tested.

Requirements:

- **Diversity over count.** The 20+ must span categories (dev tool,
  consumer app, B2B, physical, free/OSS), sources, and price models.
  Twenty dev tools is not a validation set.
- **Train/validation split.** Profiles and priors are estimated on a
  training portion and scored on a **held-out** portion. Calibration
  quality is reported on the held-out set only.
- **Pre-registered metrics.** Define the error metric (e.g. calibration
  error on bucket proportions, interval coverage) *before* tuning, so we
  measure generalization, not memorization.

Until this dataset and its split plan exist and are documented, Phase 15
implementation does not start.

---

## 9. Anti-overfitting (hard rule)

The single most important constraint on this entire phase:

> **At no stage do we tune, calibrate, or redesign the system to make one
> product pass in isolation.**

- Any global model change must be supported by a **repeated failure
  pattern across multiple validation cases** — or it must be explicitly
  labeled product/category/source-specific and scoped accordingly.
- Optimize for **generalizable market fidelity**, never benchmark
  performance on a single product.
- A change that improves one launch's number and is not validated across
  the held-out set is rejected, even if it "looks better."
- Do not fake accuracy. Do not overclaim. When the model is uncertain, the
  report says so (wide interval, source-limitation note) rather than
  inventing precision.

---

## 10. Explicit non-goals for Phase 15

- Not a rewrite of the debate/simulation engine.
- Not a change to the deep-agent count (stays 21–30) or the 100-voter
  overlay count (stays fixed at 100).
- Not touching the token/credit system (owned separately by the cofounder).
- Not Phase 13 / behavioral_mind_layer / `ASSEMBLY_BEHAVIORAL` — that work
  is explicitly out of production and unrelated to calibration.
- Not a public claim of accuracy until the held-out validation numbers
  support it.

---

## 11. Sequencing summary

1. **Ship Phase 14B.** (Prerequisite.)
2. **Build the validation dataset (20+ diverse launches) + split + metrics.**
   (Prerequisite. No calibration code before this.)
3. Estimate category priors and source-bias profiles on the training split.
4. Add action-signal weighting; measure on held-out.
5. Add private-traction anchoring (optional founder input).
6. Assemble the calibrated forecast layer behind a flag, with confidence
   intervals and full audit trail.
7. Validate on held-out set against pre-registered metrics; only then
   surface calibrated numbers in the founder-facing report.

---

*Design note only. Nothing in this document authorizes implementation.
Implementation begins after Phase 14B ships and the §8 validation dataset
plan is in place.*
