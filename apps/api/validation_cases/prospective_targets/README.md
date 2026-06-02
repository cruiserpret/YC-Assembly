# Prospective targets — sourcing intel, NEVER validation data

Phase 16A (Prospective Accuracy Sprint) sourcing intel: REAL market tests whose
outcomes are **not yet known**, for which Assembly *could* lock a prediction
*before* the outcome.

**These files are NEVER loaded as validation cases.** Like `candidates/` and
`mapping_proposals/`, this directory is **deliberately absent from
`manifest.json`**, so `loader.load_all_cases()` never reads it. Nothing here is a
prediction, a lock, or an observed outcome — it is a shortlist for human review.

A **pending validation case** is created only *after* a real prospective Assembly
prediction is run and locked (Phase 14C durable artifacts + Phase 15I bridge),
under the cost gate — never from this file.

See `docs/PHASE_16A_PROSPECTIVE_ACCURACY_SPRINT.md` for the cost gate, the
prediction-lock workflow, the scoring protocol, and the approval request.

**Caveat (important):** every target sourced so far is **buyer-numerator-only**
(final backers/pledged, or registered participants) — a one-sided
buyer/action-positive count with no non-buyer buckets and no addressable-market
denominator. These support **partial (buyer-anchor) prospective scoring only**;
they cannot become a measured `direct_observed_distribution` and therefore do
**not** unblock the Phase 15E direct-observed ≥20 bar. They are still valuable:
the project's **first real prospective prediction locks**, testing buyer-side
calibration.
