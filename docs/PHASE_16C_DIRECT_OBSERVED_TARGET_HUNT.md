# Phase 16C — Direct-Observed Target Hunt

**Status:** read-only research + planning. **No** paid predictions, **no** Assembly runs, **no** locks, **no**
pending cases, **no** observed outcomes, **no** calibration, **no** forecast change. Sourced **2026-06-03**.
Main at authoring: `9d21f80`.

## Why this phase

We hold 2 prospective pending clean-holdout locks (Hollowed Oath, Tomo) — both **buyer-numerator-only**
Kickstarters. A buyer numerator (final backers/pledged) is a one-sided action-positive count with **no**
visibility into the receptive / uncertain / skeptical remainder and **no** defensible addressable-market
denominator. It can only ever yield a **partial buyer-anchor** score and can **never** become a measured
`direct_observed_distribution`, so it does **not** unblock the Phase 15E ≥20-direct-observed bar.

This phase uses the wait time (Hollowed Oath outcome on/after 2026-06-04; Tomo on/after 2026-06-21) to hunt
for **higher-value** future targets: ones whose public outcome may expose a real **denominator** and **multiple
response buckets** (toward a future `direct_observed_distribution`), or at least stronger **Tier-1/Tier-2**
action evidence with a knowable denominator — and to do it **before** any outcome is known, so a later lock is
genuinely prospective.

## How it was sourced (read-only multi-agent workflow)

`Workflow: phase-16c-direct-observed-target-hunt` (17 agents, ~13.7 min, 0 paid Assembly runs):

1. **Scout** — 3 parallel general-purpose web agents: a *direct-observed-distribution* scout, a *strong-action-signal*
   (Tier-1/2) scout, and a *non-Kickstarter-diversity* scout. Each returned structured candidates with working
   public source URLs + an honest observability classification. → **15 candidates**.
2. **Audit** — a red-team observability auditor swept the pooled set and rejected/downgraded anything
   already-known, non-public, denominator-less, vanity-only, un-recheckable, or fabricated. → **3 rejected, 12 kept**.
3. **Verify** — one **independent** web verifier per kept target re-confirmed, from scratch, that the outcome is
   genuinely future/unknown as of 2026-06-03 and the source resolves publicly. → **12/12 survivors** (both
   mandatory gates pass), with honest per-target downgrades recorded.
4. **Rank** — a final agent ranked survivors by the strict priority cascade: (1) outcome unknown → (2) public
   source → (3) short horizon (≤2026-09-30) → (4) denominator clarity → (5) non-Kickstarter diversity →
   (6) action-signal tier → (7) full-distribution potential.

The machine-readable companion (all 12 survivors + 3 rejected, with scout/audit/verify classifications and
risk flags) is `apps/api/validation_cases/prospective_targets/phase_16c_targets.json`
(`purpose: prospective_sourcing_intel_not_validation_data`, **absent from `manifest.json`** → never loaded as a
validation case).

## Honest verdict on direct-observed potential

**Only ONE survivor is a plausibly-true `direct_observed_distribution`: State of CSS 2026.** It publishes, free
and public, a per-feature **usage** distribution (Used it / Heard of it / Never heard of it) + **sentiment**
(Positive / Neutral / Negative) over a real respondent denominator (2025 = 5,506). That maps onto Assembly's four
buckets about as cleanly as anything available — **but** (a) the denominator is **platform-self-selected**
developers, not a representative census, and (b) the bucket mapping is an **interpretation** of native
usage/sentiment labels, not native four-bucket data.

Everything else is weaker on honest inspection:
- **WunderGraph State-of-Federation 2026** — a second full-distribution survey, but **vendor-run/partisan**
  (WunderGraph competes with Apollo) and the prior report reads like a curated marketing summary; granular
  four-bucket data is not guaranteed.
- **ARC-AGI Kaggle competitions** — produce a **performance-score** distribution across teams, **not** a
  market-reaction distribution; no native buyer/receptive/uncertain/skeptical mapping.
- **All others** (Slate preorders, Concordia, Earthborne, the Product Hunt items, YC S26, XPRIZE) are
  **buyer-numerator / action-signal** — i.e. structurally the same shape as our existing Kickstarter locks, or a
  single-winner pick.

**So: no survivor is a clean native four-bucket market-reaction distribution.** State of CSS is the closest
legitimate approximation and the only candidate that could plausibly push past the buyer-anchor ceiling.

## Top 5 ranked targets (for a FUTURE paid lock — after Hollowed Oath is scored)

| # | target_id | category | outcome window | honest observability | denominator | honest scoring | score |
|---|-----------|----------|----------------|----------------------|-------------|----------------|-------|
| 1 | `devographics_state_of_css_2026` | dev survey (full dist.) | ~Jul–Sep 2026 | **direct_observed_distribution_candidate** | platform self-selected (~5.5k) | **full four-bucket (interpretive)** | 88 |
| 2 | `slate_auto_preorders_2026` | EV non-refundable preorders | ~Jul 2026 | strong_action_signal (Tier-1) | exposed audience known (~160k reservations) | partial conversion | 84 |
| 3 | `kaggle_arc_agi3_2026` | public AI competition | 2026-09-30 (milestone) | direct_observed_distribution_candidate* | self-selected team cohort | partial conversion (*perf-score, not market-reaction*) | 80 |
| 4 | `wundergraph_state_of_federation_2026` | dev survey (full dist.) | Q2–Q3 2026 (soft) | direct_observed_distribution_candidate | platform self-selected (~1k, partisan) | full four-bucket (not guaranteed) | 70 |
| 5 | `producthunt_monthly_june_2026` | Product Hunt leaderboard | ~2026-07-01 | strong_action_signal | platform self-selected | partial conversion | 64 |

\* ARC-AGI-3's "full four-bucket" was **downgraded** by the verifier to a performance-score / partial-conversion
signal — it has a strong public denominator + leaderboard but **no** native market-reaction mapping.

### #1 — State of CSS 2026 (`devographics_state_of_css_2026`)
- **Source:** https://survey.devographics.com/en-US/survey/state-of-css/2026 → results later at `2026.stateofcss.com`
- **Outcome metric:** per-feature usage + sentiment distributions over total respondents (2025 = 5,506; 2024 = 9,704).
- **Outcome window:** survey confirmed **open** ~2026-05-21; results historically publish ~5–6 weeks after close
  (2024: ran Aug 17–Sep 7, results Oct 30). Most plausibly **Jul–Sep 2026**, within horizon. **2026 results not yet published** (confirmed).
- **Risk flags:** exact close/results date unconfirmed from a primary source (survey page 403s to fetch); the
  echoed "results June 30" timeline could not be verified; self-selected denominator; bucket mapping is interpretive;
  volunteer cadence could slip past 2026-09-30.

### #2 — Slate Auto preorders (`slate_auto_preorders_2026`)
- **Source:** https://techcrunch.com/2026/05/28/slate-auto-will-announce-pricing-and-take-preorders-for-its-ev-on-june-24/
- **Outcome metric:** **non-refundable $300 preorders** + conversion of an existing **~160,000-reservation** base
  (Slate's self-reported figure) → a true reservation→paid **conversion ratio** if press breaks it out.
- **Outcome window:** preorders open **2026-06-24**; existing-holder conversion window ~closes **2026-07-24**;
  figures plausibly surface Jul–Sep 2026.
- **Risk flags:** **numerator disclosure not guaranteed** (if Slate stays silent it degrades to buyer-anchor-only);
  no fixed report date; reservation base is self-reported (some press skeptical); two distinct paid actions ($300
  walk-up vs $250 conversion) muddy a clean ratio; final price (revealed June 24) is an unknown that could swing it.

### #3 — ARC-AGI-3 milestone (`kaggle_arc_agi3_2026`)
- **Source:** https://arcprize.org/competitions/2026/arc-agi-3 · https://www.kaggle.com/competitions/arc-prize-2026-arc-agi-2
- **Outcome metric:** public Kaggle leaderboard over a known team cohort; **Milestone #2 public checkpoint 2026-09-30**
  (full final ~Nov 2, results ~Dec 4).
- **Why ranked high then caveated:** Tier-1, public denominator, fixed in-horizon checkpoint, strong category
  diversity — **but** it yields a **performance-score** distribution, not a market-reaction one (verifier downgraded
  `full_four_bucket` → `partial_conversion`). Scores are floored near zero (frontier ~0.4–1.2%), so the distribution
  may stay degenerate by Sept 30.

### #4 — WunderGraph State of Federation 2026 (`wundergraph_state_of_federation_2026`)
- **Source:** https://wundergraph.com/state-of-graphql-federation/2026
- **Outcome metric:** aggregate distribution report (adoption drivers, vendor choices, barriers) over ~1,000+
  respondents; 2026 public report targeted **Q2 2026**.
- **Risk flags:** vendor-run/partisan self-selected audience; soft "aim to publish Q2 2026" (could slip); prior
  report reads as curated marketing; smaller, less representative sample than Devographics.

### #5 — Product Hunt June 2026 monthly leaderboard (`producthunt_monthly_june_2026`)
- **Source:** https://www.producthunt.com/leaderboard/monthly/2026/6
- **Outcome metric:** final June 2026 ranking + per-product composite score; resolves ~**2026-07-01** (very short horizon).
- **Risk flags:** composite **score captures positive action only** (no receptive/uncertain/skeptical), so it is
  **not** a true four-bucket distribution (verifier downgraded `direct_observed_distribution` → `strong_action_signal`);
  self-selected voter denominator; page often shows only top-10.

## Excluded survivors (7 — kept but below the top 5)

- `kaggle_arc_agi2_2026` — Tier-1 with the **strongest** denominator+distribution profile of the set, but resolves
  **Nov 2 → Dec 4 2026, beyond the 2026-09-30 horizon** (C3 is checked before C4/C6/C7).
- `producthunt_orbit_awards_2026_cycle` — no published next-cycle date (horizon not guaranteed), no denominator, Tier-3.
- `gamefound_concordia_special_edition_2026` — Awaken Realms board game (Gamefound), launches 2026-06-09; buyer-anchor,
  exact close date unconfirmed.
- `kickstarter_earthborne_trailblazer_2026` — the only pure Kickstarter; buyer-anchor-only, structurally duplicative of
  existing locks (already 261% funded; goal signal resolved); closes 2026-06-16.
- `producthunt_fundraisly_2026` — most of the action signal (#1 daily, ~1,031 upvotes, badges) is **already public**;
  thin remaining unknown; buyer-numerator-only.
- `yc_summer_2026_batch` — Demo Day 2026-09-10, but outcomes are **invite-only / undisclosed**; observable signal is
  batch sector *composition*, not a market reaction; subjective to score.
- `xprize_wildfire_2026` — tiny field (5 finalists), single-winner pick, Q3-2026 announcement could slip past 09-30.

## Rejected by the auditor (3)

- `stackoverflow_developer_survey_2026` — **highest** distribution+denominator quality (49k+ respondents) **but** the
  2025 edition published **Dec 29** (~6-month lag) and a 2026 fielding isn't confirmed → almost certainly resolves
  **after** the horizon. Park as a long-horizon backlog item.
- `producthunt_daily_leaderboard_launch_2026` — composite "Score" (votes + discussion + activity), not a real response
  distribution → vanity-only.
- `kickstarter_game_changer_home_edition_2026` — Dropout/Game Changer; real and live but **buyer-anchor-only** and
  duplicative; closes ~June 5–6 2026 (near-immediate, and same shape as existing locks).

## Recommendation

1. **Wait for Hollowed Oath scoring (on/after 2026-06-04) before running any new paid lock.** Let the project's first
   *real scored* prospective result land before spending again — it tells us whether buyer-anchor locks are even
   worth repeating, and it costs nothing to wait.
2. **Best non-Kickstarter next lock after Hollowed Oath: `devographics_state_of_css_2026`** — the only survivor that
   could plausibly yield a (interpretive) four-bucket `direct_observed_distribution` with a real denominator, within
   horizon, and maximally diverse from our two game-Kickstarter locks. **`slate_auto_preorders_2026`** is the strongest
   pure action-signal alternative (Tier-1 conversion on a known reservation base) if a cleaner denominator is preferred
   over distribution potential.
3. **All of these beat "War of the Dragon"** (a third buyer-numerator Kickstarter) on pipeline value — State of CSS and
   ARC-AGI add distribution/denominator structure a Kickstarter can never provide. War of the Dragon, like any paid run,
   still needs **fresh explicit per-target approval** regardless.
4. **Phase 15E remains BLOCKED** and nothing here changes that: 8 cases (<20), 0 Tier-1/2 *scored* outcomes, **0
   direct_observed_distributions**. Even State of CSS would only *become* a direct-observed case **after** it's locked
   prospectively, the results publish, and the mapping passes the Phase 15L-C gates — none of which has happened.

## Anti-overfit / safety reminder

This is research only. No model was tuned, no calibration ran, no forecast logic changed, Phase 13 and the token
system were untouched, and no accuracy claim is made. A model change still requires **repeated measured failure
patterns across ≥2 distinct scored products** (`GLOBAL_FIX_THRESHOLD = 2`) — not reachable until real outcomes are
scored. Any future lock from this list requires explicit per-target approval and runs under the cost gate
(see `feedback_confirm_real_money_runs`).
