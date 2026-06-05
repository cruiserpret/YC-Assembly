# Phase 16D — Buyer-Underprediction Diagnosis Framework

**Status:** READ-ONLY planning. **No** model change, **no** calibration, **no** forecast/prompt change,
**no** new prediction, **no** outcome added, **no** Tomo scoring, **no** Phase 13 / token change, **no**
accuracy claim. This document exists so that IF Tomo also converts strongly we diagnose rigorously
instead of panic-tuning or overfitting. It changes nothing in the model or the ledger.

---

## 1. What happened on Hollowed Oath

Assembly locked a prospective prediction for the Hollowed Oath Kickstarter (run `7ed43d56…`) **before**
the outcome, then the campaign closed:

| | Locked prediction | Actual outcome |
|---|---|---|
| buyer/action-positive | **0.0%** | 698 paying backers · $166,070 · 166% of $100k · first $150k stretch cleared |
| receptive | 54.17% | *unobserved* |
| uncertain/proof-needed | 37.5% | *unobserved* |
| skeptical/resistant | 8.33% | *unobserved* |

**Verdict:** a **directional buyer-anchor MISS** — the locked 0.0% buyer/action-positive is contradicted
by a campaign that drew 698 paying backers. Recorded as `partial` / `action_anchor_only` (Phase 16B-R);
`observed` stays null.

### Why this is a buyer-anchor miss, NOT a full four-bucket miss
A Kickstarter close gives only a **buyer/action numerator over a self-selected funnel**. The receptive /
uncertain / skeptical remainder is **never observed** — those three buckets are mathematically
unidentified from a buyer-only sample (Phase 15L-A finding). So we can only say the buyer **direction**
was wrong; we cannot compute a four-bucket MAE, and we did not invent the other buckets. The "miss" is
strictly: *predicted ~0% would act, but a material number acted.*

### Why NO model change is allowed from this one result
1. **One case.** The anti-overfit law (`GLOBAL_FIX_THRESHOLD = 2`) requires the **same** directional
   failure across **≥2 distinct scored products** before any change is even *considered*.
2. **It's a partial.** A buyer-anchor partial scores one bucket's direction, not the distribution; it is
   weaker evidence than a measured four-bucket outcome and cannot be calibrated against.
3. **Single-source.** Both pending locks are Kickstarter video games — a change driven by one of them
   would be a Kickstarter/category artifact, not a validated global improvement.

### What WOULD count as a repeated failure pattern (after Tomo)
A *candidate* repeated pattern requires **all** of: (a) Tomo is **also** a strong buyer-action miss
(locked ~0% buyer, but materially funded), (b) the **same causal mechanism** plausibly explains both,
(c) **no leakage** and **no source/brief defect** unique to one case. Even then it is a *hypothesis to
investigate in a dedicated diagnostic phase* — never an automatic tune.

### How to avoid overfitting to Kickstarter
- Treat Kickstarter as **one source type**, not the world. Two Kickstarter game misses ≠ a global buyer
  bias; at most they suggest a **crowdfunding/source-specific** interpretation issue.
- Require a **held-out validation plan** and **non-Kickstarter** diversity (e.g. State of CSS, Slate)
  before trusting any change generalizes.
- Never adjust a global buyer prior from a cluster of one platform + one genre.

---

## 2. Failure hypotheses

Grounded where possible in the **locked Hollowed Oath artifact** (read-only;
`_audit/live_runs/7ed43d56…/`). Key observed facts used below:
- `buyer = 0.0%` in **all three** model representations: `lightweight_voter_distribution`
  (0 / 47.4 / 43.8 / 8.8), `calibrated_distribution` (0 / 50.8 / 40.7 / 8.6), and
  `raw_24_distribution_percent` (0 / 54.2 / 37.5 / 8.3) — so buyer=0 is a **model** property, not just
  the conservative 15I lock-mapping.
- `intent_signal_distribution`: `explicit_waitlist_or_signup: 6`, `price_blocked: 7`,
  `curious_but_unconvinced: 7`, `positive_interest_if_proven: 1`, `explicit_rejection: 2`,
  `competitor_loyal: 1` — the **highest-intent category present is "waitlist/signup", routed to
  receptive, not buyer**.
- `calibrated_distribution.calibration_warnings: ["calibration_support_weak:no_prior_cases_for_category"]`
  and `used_prior_correction: false` — **no crowdfunding/category prior existed** to lift buyer.

> Fix-scope legend: **G** = global model change, **C** = category-specific, **S** = source-specific
> (e.g. crowdfunding). Min cases = scored cases showing the pattern before acting (never < 2).

### H1 — Assembly lacks a separate crowdfunding/action prior  *(strongest current hypothesis)*
- **Description:** crowdfunding backers *pay for an unproven promise*; the model has no prior that, on
  Kickstarter, "interested/waitlist/considering-if-proven" voters convert to **paying backers** at a
  non-trivial rate. `no_prior_cases_for_category` + `used_prior_correction:false` confirm no correction
  was available.
- **Supports it:** buyer=0 with `explicit_waitlist_or_signup`=6 routed to receptive; the missing
  category prior; the report references "pledge" heavily yet predicts 0 buyers.
- **Refutes it:** if Tomo (also crowdfunding, but a *different* run with possibly a category prior) still
  predicts buyer≈0 *and* the funded reality is also a miss, but a non-crowdfunding case predicts buyer
  well → points to a source gap (supports H1). Conversely, if buyer misses appear on **non-crowdfunding**
  sources too → it's not crowdfunding-specific (refutes H1, supports H6).
- **Fix scope:** **S** (crowdfunding source prior) · **Min cases:** ≥2 crowdfunding misses + ≥1
  non-crowdfunding contrast.

### H2 — The buyer/action bucket is too strict (structurally near-unreachable)
- **Description:** the synthetic taxonomy's strongest signal ("waitlist/signup", "interest_if_proven")
  never routes to buyer, so buyer≈0 is almost guaranteed for a pre-launch product — making the bucket
  near-impossible to hit for any campaign that *does* convert.
- **Supports it:** buyer=0 across all three representations; `high_intent_segments_count=3` yet 0 buyer.
- **Refutes it:** any locked case (past or future) that predicts buyer materially > 0 shows the bucket is
  reachable (would weaken H2).
- **Fix scope:** **G** (bucket definition/routing) — *highest overfit risk, highest bar.*
  **Min cases:** ≥3–4 scored across ≥2 sources (a global routing change must not rest on 2 Kickstarters).

### H3 — Treats "needs credibility / proof" as non-buying, even when crowdfunding users still buy
- **Description:** `positive_interest_if_proven` / `would_consider_if_proven` → receptive; but
  crowdfunding's entire premise is paying *before* proof.
- **Supports it:** consider_if_proven (10) + waitlist (3) = 13 → receptive, 0 → buyer, while 698 backed.
- **Refutes it:** a crowdfunding case where proof-needed voters did **not** convert (campaign failed)
  while the model also said ~0 buyer → the conservatism was *correct* there.
- **Fix scope:** **S/C** · **Min cases:** ≥2 with the same proof-needed-but-funded shape.

### H4 — Under-weights niche/community fandom willingness to back early
- **Description:** genre/community fandom (MMORPG revival, ex-EverQuest devs) drives early backing that
  generic intent modeling misses.
- **Supports it:** strong fan-press coverage; backing despite "unproven".
- **Refutes it:** if fandom signal was present in evidence yet the campaign still under-converted, or if
  a low-fandom crowdfunding case converts equally → fandom isn't the driver.
- **Fix scope:** **C** (genre/community prior) · **Min cases:** ≥2 high-fandom + ≥1 low-fandom contrast.

### H5 — Under-weights platform-native buyer intent (waitlist/signup = action on KS)
- **Description:** on Kickstarter a pledge IS the buy; "explicit_waitlist_or_signup" should carry buyer
  weight, not pure receptive.
- **Supports it:** `explicit_waitlist_or_signup=6` routed to receptive; buyer=0.
- **Refutes it:** if reclassifying waitlist→buyer would overshoot on a *failed* campaign (false buyers).
- **Fix scope:** **S** (source-conditioned routing of action signals) · **Min cases:** ≥2 + a failed-
  campaign control so we don't simply inflate buyer everywhere.

### H6 — Assembly over-weights proof-needed / uncertainty globally
- **Description:** a *global* tilt toward uncertain/receptive regardless of source.
- **Supports it:** uncertain 37.5–43.8% here.
- **Refutes it:** the non-Kickstarter scored cases (seed set) do **not** show systematic buyer
  under-prediction → the tilt is source-specific, not global (this would refute H6 and redirect to H1).
- **Fix scope:** **G** — *very high bar* · **Min cases:** ≥4 across ≥3 sources.

### H7 — Cost-cap truncation / repair distorted the final buyer ballots
- **Description:** the $10 discussion cap truncated final ballots (repaired 2→24); maybe repair
  suppressed buyer voters.
- **Supports it:** truncation did occur.
- **Refutes it (currently LIKELY refuted):** buyer=0 is **identical** in `raw_24`, `lightweight_voter`,
  and `calibrated` — all post-repair — so the zero is not a repair artifact; the pre-ballot/reflection
  stages completed 24/24 and run_quality was 1.0/READY.
- **Fix scope:** N/A (process, not model) · **Min cases:** check per-case; here it does **not** explain it.

### H8 — Brief construction omitted platform-traction cues
- **Description:** the neutral brief deliberately excluded the live funding tally (anti-leakage); maybe
  it also under-described momentum, depressing buyer.
- **Supports it:** brief excluded the tally by design.
- **Refutes it:** excluding the *outcome* is REQUIRED for a clean prospective lock; momentum cues that
  are pre-outcome were available via retrieval. If the brief was neutral-but-complete, this is weak.
- **Fix scope:** brief-quality (process), not model · **Min cases:** review both briefs side by side.

### H9 — Evidence retrieval captured interest but not buyer-probability conversion
- **Description:** retrieval surfaced discussion/interest (Tier-3) but little revealed-action (Tier-1)
  evidence, so the model had no basis to route to buyer.
- **Supports it:** evidence-heavy report, action-light; `no_prior_cases_for_category`.
- **Refutes it:** if retrieval *did* contain Tier-1 backer/pledge-rate signals that the model ignored →
  it's a modeling gap (H1/H5), not a retrieval gap.
- **Fix scope:** **S** (retrieval/evidence-tier emphasis) · **Min cases:** ≥2 + an evidence audit.

**Strongest current hypothesis:** **H1** (no crowdfunding/action prior) — directly evidenced by
`no_prior_cases_for_category` + `used_prior_correction:false` + waitlist/signup routed to receptive —
closely related to **H5** (platform-native action routing) and **H2** (bucket too strict). **Uncertainty
is HIGH**: this is **one** partial case; H7/H8 look refutable, but H1/H2/H3/H5 cannot be distinguished
until at least Tomo is scored, and a *global* change (H2/H6) would need non-Kickstarter evidence.

---

## 3. Post-Tomo decision tree

Tomo locked `buyer_action_positive = 0.0`; outcome check **on/after 2026-06-21**. **Do not score early.**

### If Tomo is ALSO a strong buyer-action miss (locked ~0% buyer, but materially funded)
1. Classify as a **candidate repeated buyer-underprediction pattern** (2 cases). **Do NOT auto-tune.**
2. Open a **separate diagnostic/audit phase** (not a tuning phase).
3. Compare Hollowed vs Tomo **evidence + ballots**: were buyer-positive voters **suppressed** (present
   then repaired away) or **absent** (never generated)? (HO: absent, not suppressed — see H7.)
4. Inspect whether **receptive voters should have been buyer/action** under a crowdfunding interpretation
   (H3/H5), and whether **crowdfunding source-type needs separate interpretation** (H1).
5. Check both for **shared causal mechanism** and **rule out** leakage / brief defect / source anomaly.
6. Only if a shared mechanism survives → propose a **source/category-scoped** change with a **held-out
   validation plan** (predict-before-outcome on NEW cases). Still no global change without non-KS proof.

### If Tomo is NOT a buyer-action miss (e.g. it under-funds, or buyer direction is right)
- **Do not generalize from Hollowed.** Keep the HO miss as **isolated / uncertain**.
- Continue prospective locks with **better target diversity** (non-Kickstarter).
- No model change; no calibration.

### If Tomo is INCONCLUSIVE (ambiguous outcome, unclear numbers, or can't be cleanly scored)
- Hold the **no-change** posture.
- Prioritize a **non-Kickstarter** target next — **State of CSS 2026** (possible direct-observed
  distribution) or **Slate Auto** (Tier-1 conversion) — to get a different source/denominator before any
  pattern claim.

---

## 4. Model-change gates (ALL required before ANY change is enabled)

1. **≥2 scored cases** showing the **same directional** failure pattern (`GLOBAL_FIX_THRESHOLD = 2`).
2. A **clear shared causal mechanism** (not two coincidental misses).
3. **No leakage** and no prediction-lock integrity problem in any contributing case.
4. **No source-specific anomaly** masquerading as a general pattern (or, if source-specific, the fix is
   scoped to that source — not global).
5. **No evidence the issue is only brief quality** or retrieval/process (H7/H8/H9 ruled out).
6. A **held-out validation plan**: the proposed change must be specified, then validated by
   **predicting BEFORE outcomes** on NEW locked cases — never judged on the cases that motivated it.
7. For a **global** change (H2/H6): additionally requires **non-Kickstarter** corroboration (≥3 cases,
   ≥2–3 source types). Source/category changes (H1/H3/H4/H5) require ≥2 of that source/category.

---

## 5. What NOT to do (explicitly forbidden)

- ❌ Increasing buyer probabilities manually.
- ❌ Changing bucket definitions or routing from one case.
- ❌ Calibrating from buyer-anchor **partials** alone (they carry no four-bucket truth).
- ❌ Treating `action_anchor_only` as `direct_observed_distribution` or counting it toward the Phase 15E
  ≥20 bar.
- ❌ Using the Hollowed outcome to retroactively tune prompts, briefs, or evidence weighting.
- ❌ Adding product-name / company / source-specific **hacks** (a principled source *prior* validated on
  held-out data is not a hack; a hardcoded "Kickstarter → +X% buyer" patch is).
- ❌ Scoring Tomo before 2026-06-21, or editing any locked prediction / artifact.

---

## 6. Lightweight data-check findings (read-only; nothing modified, nothing re-run)

From `_audit/live_runs/7ed43d56…/` (the locked Hollowed Oath run):
- **Where the model was conservative:** it assigned **0% to buyer/action-positive across every internal
  representation** (rich-24, 100-voter, calibrated). The strongest synthetic intent category generated
  was `explicit_waitlist_or_signup` (6 / 24) — an *action-adjacent* signal — yet it was routed to
  **receptive**, and `positive_interest_if_proven` / `would_consider_if_proven` → receptive as well.
- **Why it couldn't self-correct:** `used_prior_correction = false` and
  `calibration_warnings = ["calibration_support_weak:no_prior_cases_for_category"]` — there was **no
  prior** for this category/source to lift any mass into buyer.
- **What this is NOT:** not a repair artifact (buyer=0 is identical pre/post-repair representations), not
  a leakage issue (lock pre-dates outcome, hash self-reproduces), and **not yet a validated bias** — it
  is a single, partial, single-source data point. It motivates **H1/H5/H2** as the lead hypotheses to
  test against Tomo, and nothing more.

---

## Summary

Hollowed Oath is a **single directional buyer-anchor miss** with a clear, evidence-grounded lead
hypothesis (no crowdfunding/action prior → buyer bucket structurally ~0). That is a **hypothesis to
test, not a defect to fix.** This framework defines exactly what to inspect, what would count as a real
repeated pattern, the gates any change must clear, and the things we will not do — so that if Tomo also
misses we run a disciplined diagnosis instead of overfitting two Kickstarter games into a global tune.
The model and the ledger are unchanged.
