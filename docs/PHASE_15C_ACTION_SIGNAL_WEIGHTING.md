# Phase 15C — Action-Signal Weighting (Evidence Representation Layer)

Part of the Phase 15 calibration roadmap
([scope](PHASE_15_MARKET_EVIDENCE_AND_CALIBRATION_SCOPE.md),
[ledger](PHASE_15B_VALIDATION_LEDGER.md)). Phase 15C adds a small,
deterministic layer that distinguishes **what people SAY** from **what people
DO**, and represents each piece of evidence with a tier and a heuristic
strength.

> **It changes no forecast.** Phase 15C is *representation only*: it classifies
> and summarizes evidence. It applies no calibration, emits no
> buyer/receptive/uncertain/skeptical distribution, and touches no live output.
> Calibration and correction come later (15D/15E), only after enough validation
> cases and holdout discipline. **The weights here are heuristic defaults, never
> tuned to the 6 seed cases or any observed outcome.**

Code: `apps/api/src/assembly/market_calibration/` (`action_signals.py`,
`signal_weights.py`). Optional, backward-compatible ledger hook:
`ValidationCase.action_signals`.

## Why comments are strong for "why" but weak for "how many"

Public comment threads are where Assembly is already strong: they reveal the
*objections*, the *proof needs*, and the *language* of a market — the **why**.
But a comment is one loud voice, not one unit of demand. Threads
over-represent the opinionated, the in-category, and the performative, and
under-represent everyone who quietly acted (or didn't). So comment proportions
are a biased estimator of market proportions — strong for **why**, weak for
**how many**.

## Why purchases / backers / signups / stars / installs matter differently

Behavior is a far better proxy for *how many* than opinion is. A person who
**purchased**, **backed**, **signed up**, **installed**, or **forked** has
revealed real demand; a person who **upvoted** or **starred** has revealed
interest; a person who **commented** has revealed only an opinion. Phase 15C
encodes this ordering as tiers so the later calibrated model can weight
revealed action above opinion when estimating proportions.

## How the tiers work

| Tier | Meaning | Examples |
|---|---|---|
| **1** | revealed action (strongest) | purchase, paid signup, backer pledge, trial conversion, demo request, install/download, GitHub fork (dev tools), retention/churn |
| **2** | semi-action | GitHub star, PH upvote/follow, waitlist signup, Discord join, bookmark/share, traffic, search interest |
| **3** | public opinion | comment sentiment, public praise/criticism, forum/social discussion, reviews |
| **4** | synthetic | deep-agent forecast, 100-voter forecast, behavioral-layer forecast (only if validated) |

- `classify_action_signal(signal)` returns the canonical tier for a known
  `signal_type`; an unknown type may carry an explicit `tier` (custom signal)
  or remain unclassified.
- `default_signal_strength(signal_type, source_type, product_category)` returns
  a heuristic strength in [0,1]: a tier base (1.0 / 0.6 / 0.3 / 0.3) with a few
  **generic** adjustments — GitHub signals count for dev/OSS products and are
  discounted elsewhere; Product Hunt upvotes carry novelty/social noise;
  traffic/search are noisy proxies. These adjustments are **domain knowledge,
  not outcome-fitted**.
- `aggregate_action_signals(signals)` returns an **evidence profile** — counts
  and strength by tier, the strongest tier present, a strength-weighted
  dominant direction, and an overall confidence. It deliberately emits **no
  market-proportion forecast**.
- Helpers: `evidence_tier_summary`, `has_tier1_action_evidence`,
  `action_signal_confidence`.

## How this helps Tiiny-like failures

The clearest miss in the validation ledger is **Tiiny**: the production
forecast predicted **0% buyers**, but the real Kickstarter outcome had ~30%
buyer/action-positive — because **backers are buyers**, and a stated-caution
comment thread hid that. Phase 15C gives the system the vocabulary to record a
`kickstarter_pledge` as a **Tier-1 action** with its count/denominator, so a
later calibrated model can let revealed backing outweigh comment caution. The
same applies to dev tools (GitHub forks/stars), SaaS (signups/trials), and
consumer apps (installs). Phase 15C does **not** apply that correction yet — it
makes the correction *possible* by representing the evidence honestly.

## How this feeds Phase 15D (source / category priors)

Phase 15D learns **source-bias profiles** and **category priors** on the
training split of the ledger and validates them on holdout. It needs a
structured representation of *what evidence each case carried, from which
source, at which action tier* — exactly what the `ActionSignal` schema and the
optional `ValidationCase.action_signals` hook provide. 15C builds the
vocabulary; 15D estimates the weights from data (replacing the heuristic
defaults here); 15E assembles the calibrated forecast; 15F scores it on
holdout.

## Why this does NOT yet claim calibrated accuracy

Nothing here has been validated against held-out outcomes. The tier ordering
and strengths are **theory-grounded defaults**, not learned weights, and the
validation ledger currently has **no clean holdout set** (all six seed cases
are training, and their observed data is Tier-3/comment-derived). Until 15D
learns weights on training data and 15F beats the uncalibrated baseline on a
held-out set, Assembly makes **no calibrated-accuracy claim** — and 15C changes
no forecast, so it cannot inflate one.

## How anti-overfit is protected

- **No outcome leakage.** The `market_calibration` package does not import the
  ledger and never reads observed proportions — a test enforces both.
- **No per-product tuning.** Weights are tier/source/category heuristics with
  zero product-name conditions and zero reference to the six cases' outcomes.
- **No forecast change.** `aggregate_action_signals` provably emits no
  market-bucket distribution (a test asserts the four bucket keys never appear).
- **Pure + deterministic.** No LLM, network, DB, or randomness (enforced by an
  import-allowlist test).
- **Defaults are explicitly provisional.** They exist to represent evidence and
  are intended to be replaced by weights *learned on a holdout set* in 15D.

---

*Phase 15C is an evidence-representation layer. It builds the vocabulary for
calibrated forecasting without performing calibration, changing forecasts, or
claiming accuracy.*
