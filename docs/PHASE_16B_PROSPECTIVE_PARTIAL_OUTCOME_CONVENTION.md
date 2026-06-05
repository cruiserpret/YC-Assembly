# Phase 16B-R — Prospective Partial Outcome Recording Convention

**Status:** ledger-data + audit-tooling layer only. **No** forecast change, **no** calibration,
**no** model change, **no** Phase 13 / token change, **no** paid run, **no** accuracy claim.

## Problem

A prospective lock (Phase 14C + 15I) records an Assembly prediction **before** the outcome is
known. When the outcome arrives, most real public outcomes (e.g. a Kickstarter close) give only a
**buyer/action numerator over a self-selected funnel** — a one-sided count with **no** visibility into
the receptive / uncertain_proof_needed / skeptical_resistant remainder, and **no** representative
denominator.

The official `observed` field is an `ObservedProportions` — a **full four-bucket distribution that
must sum to ~100**. There is **no** schema slot for a buyer-only outcome. Recording such an outcome in
`observed` would require **fabricating** the three non-buyer buckets, which the Phase 15L-A/15L-B
finding forbids (those buckets are *mathematically unidentified* from a buyer-only sample). The 15L-C
ingest bridge is also **candidate-based** and only builds **new** cases from `direct_observed` /
`assumption_labeled` distribution mappings — it has no path to attach a buyer-anchor outcome to an
existing prospective pending lock.

This convention fills that gap **without** fabricating any bucket.

## The convention

When a prospective lock's outcome is a **buyer/action anchor only** (`action_anchor_only`):

1. **`observed` stays null.** Never populate it; never invent receptive/uncertain/skeptical; never
   compute a four-bucket MAE. The non-buyer buckets remain *unobserved*, not zero.
2. **`metadata.validation_status` → `"partial"`.** The schema already supports `"partial"` (the case
   has a prediction and a *partial*, non-distribution outcome). `"scored"` is reserved for cases with
   a full `observed` distribution; `"pending"` means no outcome yet.
3. **The buyer evidence goes in `action_signals`.** Add one machine-readable `ActionSignal`
   (`extra="forbid"`, so only its existing fields are used): `signal_type` (e.g. `kickstarter_pledge`,
   which auto-classifies to **Tier 1**), `source_type`, `count` (the buyer count), `denominator`
   (null — self-selected), `direction` (`positive`/`negative`), `confidence`, `observed_at`,
   `source_reference`, and a `notes` summary. Rich detail that does not fit the signal schema
   (pledged USD, goal thresholds, goal statuses, the directional verdict) lives in the **separate
   prospective-outcome record** (below), not forced into the signal schema.
4. **The lock is untouched.** `predicted`, the whole `prediction_lock` block, the `prediction_hash`,
   `used_for_holdout=true`, `used_for_training=false`, and `leakage_risk` are **unchanged** — the
   `prediction_hash` still self-reproduces, so the lock remains cryptographic proof of the
   *pre-outcome* prediction.
5. **A separate, git-tracked, audit-only record** is written under
   `apps/api/validation_cases/prospective_outcomes/<run_id>.json` with
   `purpose: prospective_partial_outcome_not_observed_distribution`. This directory is **absent from
   `manifest.json`** and is **never loaded as a validation case** (same isolation as
   `prospective_locks/`, `prospective_targets/`, `candidates/`, `mapping_proposals/`). It records the
   full outcome (pledged, backers, goal/stretch statuses), the late-pledge caveat, the directional
   verdict, the source references, and explicit statements that `observed` stays null, the non-buyer
   buckets are unobservable, this is **not** a `direct_observed_distribution`, and it **does not**
   unlock Phase 15E.
6. **The original lock-provenance record is immutable.** `prospective_locks/<run_id>.json` stays a
   blind record of the pre-outcome prediction. The Phase 16A-P verifier still requires the **record**
   to carry no outcome/action fields; it only relaxes the *case*'s "action_signals must be empty"
   check from "always" to "while the case is still `pending`" — a partially-scored case legitimately
   carries the buyer anchor.

## What this is NOT

- **NOT** a `direct_observed_distribution`. It does not count toward the Phase 15E ≥20-direct-observed
  bar; `ledger_direct_observed_count` stays unchanged.
- **NOT** a full four-bucket score; no four-bucket MAE is computed.
- **NOT** calibration, tuning, or any forecast/model change. A model change still requires repeated
  measured failure patterns across **≥2** distinct scored products (`GLOBAL_FIX_THRESHOLD = 2`).
- **NOT** a clean-holdout disqualifier by itself: `is_clean_holdout` keys on `used_for_holdout` +
  leakage discipline (not on `validation_status`), so a partially-scored holdout lock with a valid
  pre-outcome lock and `observed=null` **remains** a clean holdout.

## Metrics, kept separate (do not blur)

- **Action-signal tier coverage** (`tier_coverage_summary`, `action_signal_coverage_summary`) reflects
  the new Tier-1 buyer anchor: a partially-scored lock shows **one Tier-1 action signal**.
- **Direct-observed distribution count** (`ledger_direct_observed_count`) is a *separate* metric and
  stays **0** — a buyer anchor is not a measured distribution.
- A real Tier-1 action outcome **does** satisfy the "≥1 ledger case with Tier-1/2 action outcomes"
  sub-requirement of `mapping_readiness`, but Phase 15E **remains blocked** on the binding constraints
  (0 direct-observed distributions, <20 cases).

## First application

`run_7ed43d56-566d-47f0-b7c3-3cee4c97ab1f` — **Hollowed Oath** (God Mode Games MMORPG Kickstarter).
Locked `buyer_action_positive = 0.0`; final close (2026-06-03) ≈ **$166,070 / 698 backers / 166% of
the $100k goal**, base goal cleared, first **~$150k** stretch cleared, 10 further stretch goals (up to
~$3.5M) unmet. Verified from public press (Massively OP, MMORPG.com). BackerKit late-pledge ongoing,
**excluded** from the locked Kickstarter-close score. Directional verdict: **buyer-anchor MISS**
(locked 0.0% vs a campaign with 698 paying backers). Partial only — the receptive/uncertain/skeptical
buckets are unobserved.
