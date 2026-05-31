# Phase 15H — Validation Case Acquisition Plan & Intake Backlog

> **Planning / intake infrastructure only.** Phase 15H adds *discipline* for how
> Assembly's validation ledger grows — it does **not** improve accuracy, apply
> calibration, change any live forecast, or add scored cases. It adds **no
> invented data**. Its only job is to make future validation-case acquisition
> disciplined, auditable, and anti-overfit so that later calibration (15E/15F)
> can be real instead of overfit.

Part of the Phase 15 roadmap
([scope](PHASE_15_MARKET_EVIDENCE_AND_CALIBRATION_SCOPE.md),
[ledger](PHASE_15B_VALIDATION_LEDGER.md),
[action signals](PHASE_15C_ACTION_SIGNAL_WEIGHTING.md),
[diagnostics](PHASE_15D_SOURCE_BIAS_DIAGNOSTICS.md),
[dataset tooling](PHASE_15G_VALIDATION_DATASET_EXPANSION.md)).

The 15G tooling can *ingest* clean cases. 15H decides **which cases to acquire,
how, and to what standard** — the demand-side plan that feeds the 15G CLI.

---

## 0. What this phase is and is NOT

| This phase DOES | This phase does NOT |
|---|---|
| Define a target dataset + diversity goals | Add any scored/holdout/pending case |
| Catalogue case types, sources, categories, outcomes | Invent observed outcomes or metrics |
| Set the minimum evidence bar for a case | Apply calibration or corrections |
| Codify anti-overfit + budget discipline | Change any live forecast |
| Provide a planning **backlog** + reviewer checklist | Claim Assembly is more accurate |
| Add an optional pure backlog validator | Run paid simulations or call an LLM |

The ledger still holds the **same 6 real seed cases**; `holdout_cases.json` and
`pending_cases.json` remain **empty** until real, externally-reviewed data exists.

---

## 1. Why Phase 15E (calibration) is blocked by data

A calibrated forecast that is *fit and scored on the same handful of cases* is
overfitting, not calibration. The current ledger cannot support honest
calibration:

- **Only 6 cases** — far below the ~20 minimum needed to separate a repeatable
  bias from noise. Every Phase 15D source/category profile is already
  `weak`/`insufficient` for exactly this reason (1–3 cases per source).
- **0 holdout cases** — a correction is only trustworthy if it improves
  predictions on cases it never saw. With nothing held out, there is nothing to
  validate against; any "improvement" is in-sample fitting.
- **0 Tier-1 action outcomes** — the seed's observed proportions are largely
  comment/sentiment analysis (Tier 3), which is biased for *how many* people
  will actually act. Calibrating real proportions needs revealed-action ground
  truth.
- **0/6 cases carry populated `action_signals`** — the action-signal
  representation (15C) has nothing real to weight.
- **Mostly Tier-3 / comment-derived data** — opinion-grade, not action-grade.

**Phase 15E does not begin until the data bar in §2 + §8 is met.** 15H is the
plan to get there without manufacturing a false accuracy claim.

---

## 2. Target dataset

| Milestone | Target |
|---|---|
| **First serious calibration test** | **≥ 20 cases** total, diverse |
| **Mature dataset (over time)** | **30–50 cases** |
| **Clean holdout split** | a meaningful, blind holdout set (new cases default to holdout) that is never used to fit a correction |
| **Source diversity** | no single source dominates; fill the gaps in §4 |
| **Category diversity** | spread across the categories in §5 |
| **Action evidence** | a real share of cases carry **Tier-1/Tier-2** revealed-action outcomes, not just comments |

Diversity is a hard requirement, not a nice-to-have: a 20-case dataset that is
all Product Hunt dev tools would still over-fit one slice of the market.

Current distribution to correct (from the 15G summary):
`sources {kickstarter:1, product_hunt:2, hacker_news:3}` ·
`categories {crowdfunding_hardware:1, developer_tools:2, consumer_apps:1, open_source_software:2}`.
**Underrepresented / missing: GitHub, Reddit, App/Chrome stores, B2B; AI
productivity, AI agents/tools, B2B SaaS, note/PKM.** The backlog (§ below)
prioritizes these gaps.

---

## 3. Case types

### A. Retrospective candidates
- Existing public product launches whose outcomes are now observable
  (e.g. a finished Kickstarter's backer count, a public GitHub star history).
- **Cheaper / $0** — no new simulation required.
- **Higher leakage risk**: because the outcome is already public, a prediction
  "reconstructed" now may have been contaminated by hindsight. These MUST be
  marked `leakage_risk` honestly (often `medium`/`high`) and carry a
  `clean_room_notes` explanation of how the prediction was isolated from the
  outcome. Many retrospective cases will be **training-only** or excluded from
  clean holdout.
- Best used to grow breadth and Tier-1/Tier-2 coverage quickly while the
  prospective pipeline (which needs 14C) is stood up.

### B. Prospective candidates
- Assembly **locks** a prediction *before* the outcome exists, then the outcome
  is observed weeks/months later.
- **Cleanest holdout** — leakage is structurally prevented by the lock.
- **Costs money** when it requires running new simulations, and **requires
  Phase 14C durable artifacts first** so the lock (run_id, evidence snapshot +
  hash, prediction hash, locked timestamp) is durably, auditably persisted. A
  paid prediction with no durable lock is wasted spend *and* a leakage risk.
- This is the gold-standard source of clean holdout cases.

---

## 4. Source targets

Spread acquisition across (schema `source_type` in parentheses):
- Hacker News (`hacker_news`)
- Product Hunt (`product_hunt`)
- Kickstarter (`kickstarter`)
- GitHub (`github`)
- Reddit (`reddit`)
- App Store / Chrome Web Store (`app_store`)
- B2B SaaS public launches (`b2b`)
- Open-source dev tools (often `github` / `hacker_news`)
- AI productivity tools (often `product_hunt` / `b2b`)

Priority for the next wave: **GitHub, Reddit, App/Chrome stores, B2B** (zero or
near-zero coverage today).

---

## 5. Category targets

- developer tools
- open-source software
- AI productivity
- AI agents / tools
- B2B SaaS
- consumer apps
- crowdfunding hardware
- note / PKM tools

Priority for the next wave: **AI productivity, AI agents/tools, B2B SaaS,
note/PKM** (zero coverage today).

---

## 6. Action-outcome targets (evidence tiers)

Capture the **highest tier available** per case; record the denominator
honestly. Tier maps to the Phase 15C action-signal taxonomy.

**Tier 1 — revealed action (strongest):**
purchases · backers · paid signups · trial→paid conversions · installs /
downloads · GitHub forks (for dev tools) · retention / churn (if public or
founder-provided).

**Tier 2 — semi-revealed:**
GitHub stars · Product Hunt upvotes / followers · waitlist signups · Discord
joins · traffic / search interest · bookmarks / shares.

**Tier 3 — opinion (weakest, what we mostly have):**
comments · reviews · social sentiment.

A case may carry signals at multiple tiers; the **observed `denominator_type`**
should reflect the real action grade (e.g. `backers`, `upvotes`) where action
data exists, not `comments`.

---

## 7. Minimum evidence required to add a *scored* case

A case may only be marked `scored` when it carries:

1. **A locked prediction** (`prediction_lock.locked_prediction_created_at`) — or,
   for a retrospective case, an explicit **clean-room reconstruction note**
   describing how the prediction was isolated from the known outcome.
2. **A source / evidence snapshot** (id and/or hash) so the input is auditable.
3. **An observed outcome** (the four-bucket proportions).
4. **A denominator** — `denominator_type` + `denominator_count` (be honest:
   `comments` is not `backers`).
5. **`action_signals`** where available (with `count`/`denominator`/`direction`),
   or an honest absence.
6. **`leakage_risk` marked explicitly** (`low`/`medium`/`high`, never `unknown`).
7. **A training/holdout split decision** (new cases default to **holdout**).
8. **Uncertainty notes** — confidence in the observation, known biases, caveats.

Cases lacking an observed outcome but with a locked prediction are **`pending`**
(go to `pending_cases.json`) until the outcome is observed. Incomplete cases are
`partial` or are left in the backlog — they are **not** promoted to `scored`.

---

## 8. Anti-overfit rules (non-negotiable)

- **No holdout → training reclassification after seeing error** unless it is
  explicitly documented *and* that case is removed from all accuracy claims. A
  case that has informed a correction is no longer blind.
- **No product-name tuning** — no logic keyed to a specific product/company.
- **No one-case calibration** — every correction must be supported by a pattern
  across multiple cases, not a single outlier.
- **No observed outcomes as a runtime model input** — observed data is used only
  to *score* locked predictions, never fed back into the live forecast.
- **No accuracy claim until a held-out win** — improvement must show on cases the
  correction never saw.
- **Every correction must improve held-out performance**, not just training fit.
  A correction that helps training but not holdout is overfitting and is rejected.

These mirror and extend the discipline already enforced at ingest by the 15G
checks (lock present, no peeking, explicit leakage risk, high-risk excluded from
clean holdout).

---

## 9. Budget discipline

- **Prioritize $0 retrospective cases first** to build breadth and Tier-1/Tier-2
  coverage without spend.
- **Do not run paid prospective simulations until Phase 14C is live** — without
  durable artifact persistence the lock is not auditable, so the spend buys an
  un-trustworthy case.
- **When paid runs begin**, durably lock the prediction artifacts (run_id,
  evidence snapshot + hash, prediction hash, timestamp) *before* observing the
  outcome. Treat each paid case as a clean prospective holdout.
- Every paid run is an explicit, approved decision — never automatic.

---

## 10. Data-acquisition workflow

For each candidate (tracked in `acquisition_backlog.json`):

1. **Identify candidate** — add to the backlog with source/category/case_type and
   the gap it fills.
2. **Confirm outcome availability** — is a real observed outcome obtainable
   (backers, stars, installs, founder-provided retention, etc.)?
3. **Confirm diversity contribution** — does it fill a source/category/tier gap?
4. **Classify evidence tier** — Tier 1/2/3, set `expected_outcome_tier`.
5. **Enter as `pending` or `scored`** via the 15G CLI
   (`scripts/phase_15g_add_validation_case.py`), defaulting new scored cases to
   **holdout**. The CLI runs the lock + leakage checks and refuses leaky cases.
6. **Run the dataset summary** (`scripts/phase_15g_validation_dataset_summary.py`)
   to confirm the gap moved and no warning regressed.
7. **External review before promotion** to `scored`/`holdout` using
   `templates/validation_case_review_checklist.md`. Only a reviewed case counts.

The backlog tracks `acquisition_status`
(`not_started → needs_review → ready_to_ingest`/`rejected`) and carries
`do_not_ingest_yet: true` on every target so nothing is ingested by accident.

---

## Backlog & tooling in this phase

- **`apps/api/validation_cases/acquisition_backlog.json`** — a **planning-only**
  backlog of candidate targets. It is **not** a validation-ledger file, is **not**
  listed in `manifest.json`, and is **never** loaded by `load_all_cases()` /
  `load_cases()`. It contains **no observed outcomes** — only *expected* tiers and
  signals to go research.
- **`apps/api/validation_cases/templates/validation_case_review_checklist.md`** —
  the external-reviewer checklist gating promotion to a real case.
- **`apps/api/src/assembly/validation_ledger/acquisition_backlog.py`** — pure,
  deterministic helpers (`load_acquisition_backlog`, `validate_acquisition_backlog`,
  `backlog_summary`). It does **not** import or touch ledger scoring and cannot
  add cases.

---

## Guard (read this)

- **This phase does not improve accuracy.**
- **This phase does not apply calibration.**
- **This phase does not add scored validation cases.** (No invented data; seed
  unchanged; holdout/pending stay empty.)
- **This phase creates acquisition discipline so later calibration is real.**

## Recommended next move after 15H

1. **Phase 14C — durable run artifacts** (parked branch exists): the technical
   prerequisite for clean *prospective* locked predictions and any future paid
   run. Land this before paying for a single prospective case.
2. **In parallel / meanwhile — acquire $0 retrospective cases** from the backlog
   (GitHub / Reddit / App-store / B2B gaps first), each externally reviewed,
   ingested via the 15G CLI as blind holdout where leakage allows.

Only once ~20 diverse, reviewed cases with a clean holdout split and real
Tier-1/Tier-2 outcomes exist does **Phase 15E** (flag-gated calibrated prototype,
alongside — never replacing — the current forecast) → **15F** (held-out scoring
vs baseline) begin. **No calibration or accuracy claim until a held-out win.**

---

*Phase 15H is planning / intake infrastructure only. It adds no fake validation
cases, applies no calibration, changes no forecast, and claims no accuracy. It
exists to make future validation-case acquisition disciplined, auditable, and
anti-overfit.*
