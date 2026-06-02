# Phase 16A — Prospective Accuracy Sprint

**Status: sourcing + infrastructure complete; NO paid prediction run; NO pending
case created; dataset unchanged (6 / 6 / 0 / 0). Held at the spend boundary
pending explicit human approval (see "Approval request"). Phase 15E remains
BLOCKED.**

This is the first **measured-accuracy** phase. The thesis: Assembly only earns
calibration by **locking predictions BEFORE outcomes**, then scoring after the
outcome arrives — never by tuning to retrospectives. Phase 16A sources real
current market tests, defines the cost-gated lock workflow, and (after approval)
locks 1–3 prospective predictions as **pending** validation cases.

## Why prospective (the whole point)

A retrospective known-outcome case can never be a clean holdout (the prediction
could have seen the outcome). The ONLY way to obtain a **clean holdout** — and the
only honest path to measured accuracy + eventually unblocking Phase 15E — is to
lock an Assembly prediction *before* a future outcome (Phase 14C durable
artifacts + Phase 15I prediction-lock bridge), then record the outcome later
through the Phase 15L-C gated mapping path.

## Target selection criteria

1. **Future outcome** — resolves on a public date strictly after the run date
   (live-verified by fetching the source page during sourcing).
2. **Short horizon** — ideally 7–30 days, so the outcome (and the score) arrives soon.
3. **Public, re-observable outcome source** — a URL we can re-check to record the metric.
4. **Action-based metric** — backers/pledged, preorders, registrations, installs.
5. **Category diversity**, **low ambiguity**, **no leakage** (outcome genuinely unknown now).

**Honest limitation of the current shortlist:** every live-verified target is
**buyer-numerator-only** (a one-sided buyer/action count, no non-buyer buckets,
no addressable-market denominator). These support **partial (buyer-anchor)**
prospective scoring and produce the first real prediction locks, but they **cannot
become a measured `direct_observed_distribution`** and therefore do **not** unblock
the Phase 15E ≥20 direct-observed bar. See the shortlist in
`apps/api/validation_cases/prospective_targets/phase_16a_targets.json`.

## Cost gate (binding — before ANY paid run)

1. Identify the exact target (from the reviewed shortlist).
2. Confirm the outcome is future / not yet known (re-fetch the source page).
3. Print the exact run command.
4. Confirm the cost guard is active.
5. Cap total paid spend at **$30** (unless explicitly edited).
6. If the cost guard is unavailable **or** the estimated cost is unclear → **STOP and ask for approval.**

**Cost guard (audited):** `assembly.llm.cost_guard.with_cost_guard` enforces a
per-simulation cumulative cap via a Postgres `SELECT … FOR UPDATE` row lock + a
`SUM(llm_call_log.cost_usd)` check; an over-cap projected call raises
`CostCapExceeded` **before** any provider API call (zero spend on refusal).
`tests/test_no_drift*.py` statically forbid bypassing it. Real observed run cost
is **~$3–6** (orchestrator estimate $3.02 / 168 calls; a real prior run's
discussion stage $2.40). Set both caps low for a belt-and-suspenders bound:

```bash
ASSEMBLY_COST_HARD_USD=12 ASSEMBLY_LIVE_DISCUSSION_BASE_CAP_USD=10
```

**Why this phase STOPPED at the boundary** (and did not auto-run): (a) it is real
external money + irreversible Anthropic/Postgres writes — confirmed first per the
project's outward-action discipline; (b) the $30 cap is a *sum of per-stage caps*,
not a single global lever (the audit flagged this — step-6 "cost unclear"); (c)
the run command forecasts a *fixed demo product*, so a real target needs a brief
authored for it first; (d) all targets are buyer-numerator-only, so the payoff of
these specific locks is partial — a human's call.

## Prediction-lock workflow (after approval)

```bash
cd apps/api
# 1. author a brief for the chosen target (model on scripts/run_*_fresh_demo.py),
#    then run ONE cost-capped prediction (this is a PAID run):
ASSEMBLY_COST_HARD_USD=12 ASSEMBLY_LIVE_DISCUSSION_BASE_CAP_USD=10 \
  ASSEMBLY_ARTIFACT_ROOT=<stable/mounted path> \
  ./.venv/bin/python scripts/run_<target>_prospective.py     # prints run_id=...
# 2. verify durable artifacts landed:
ls "$ASSEMBLY_ARTIFACT_ROOT/live_runs/<RUN_ID>/"   # founder_report.json + evidence_snapshot.json
# 3. build the PENDING case (inspect first — writes nothing):
./.venv/bin/python scripts/phase_15i_create_case_from_run.py --run-id <RUN_ID> \
  --source-type kickstarter --product-category tabletop_board_game --print-only
# 4. only after a clean --print-only, append the pending case:
./.venv/bin/python scripts/phase_15i_create_case_from_run.py --run-id <RUN_ID> \
  --source-type kickstarter --product-category tabletop_board_game
# 5. confirm: load_all_cases() gains exactly ONE pending case, observed=None,
#    used_for_holdout=true, used_for_training=false, action_signals=[].
```

A pending case may be added ONLY if: the prediction was generated before the
outcome; the artifact is durable; a prediction hash exists; observed=None;
`used_for_holdout=true`; `used_for_training=false`; `action_signals=[]`; and the
source URLs + outcome observation date are recorded. **No observed outcome is
added in this phase.**

## Provenance hardening (Phase 16A-P)

The full run artifacts under `apps/api/_audit/live_runs/<run_id>/` are **local and
untracked** (not git-tracked, not on the production volume). So **at lock time,
also write a compact git-tracked provenance record** to
`apps/api/validation_cases/prospective_locks/<pending_case_id>.json`
(purpose marker `prospective_lock_provenance_not_observed_outcome`, absent from
`manifest.json`, never loaded as a case). It captures the audit-critical facts —
`predicted_proportions`, all hashes, `locked_at`, the hash parameters
(`report_schema_version`, `simulation_id_used_in_hash`), cost + run-quality + repair
summaries, evidence-snapshot source — so the lock is **fully reproducible from git
alone** (the `prediction_hash` recomputes from the record). Verify with
`scripts/phase_16a_verify_lock_provenance.py --record <file>` (read-only). It is
**not** an observed outcome (`observed: null`) and **not** calibration data;
`pending_cases.json` stays the source of truth.

Conventions: keep large local artifacts **outside git** unless explicitly approved;
for production runs point `ASSEMBLY_ARTIFACT_ROOT` at the durable Railway volume;
scoring Hollowed Oath remains queued for **on/after 2026-06-04**.

## Scoring protocol (when the outcome arrives)

- A case becomes scorable only when **predicted AND observed** are both present
  (`is_scorable()`); then `metrics.compute_all` computes `mae_pp`, `tvd`,
  `max_bucket_error_pp`, `direction_match`, `buyer_false_confidence`,
  `objection_overlap_score`.
- **Full** four-bucket scoring needs a measured four-bucket observed distribution.
  **Partial** (buyer-anchor) scoring applies when only the buyer numerator is
  observed (the current Kickstarter targets) — we can check whether Assembly's
  `buyer_action_positive` prediction was directionally sensible, but NOT a full
  distribution.
- **Record the observed outcome LATER only through the Phase 15L-C gated bridge**
  with a reviewer-authored `ProposedOutcomeMapping` — never a hand-edit. For a
  buyer-only outcome that mapping is at best `action_anchor_only` (no four-bucket
  distribution), which by design cannot become a measured observed case.

## Anti-leakage rules

- Prediction must be locked before the outcome (`locked_prediction_created_at` set).
- `observed_at` must not predate the lock (`validate_no_outcome_leakage`).
- `leakage_risk` explicit (not `unknown`) for scored/holdout; a `high`-risk case
  is excluded from clean holdout.
- A case can never be both `used_for_training` and `used_for_holdout`.
- Provenance is forge-proof (Phase 15L-C sanitized marker + keyset-bounded parse).

## Anti-overfit rules (what justifies an actual model change)

Per `calibration/overfit_diagnostic.py` (`GLOBAL_FIX_THRESHOLD=2`): a measured
failure pattern justifies a model change ONLY if it recurs across **≥2 distinct
products** — e.g. receptive over-prediction (>+10pp), skeptic under-prediction
(>+10pp), buyer false confidence / buyer-miss, uncertain-bucket over-injection,
or a persistent wrong-argmax. Even then the diagnostic only flags `likely_systemic`
and proposes counterfactuals **diagnostically** — it never auto-mutates source
profiles or production state. **No calibration or model change happens in 16A.**

## What does / does not count toward Phase 15E

- **Counts:** a prospective **clean holdout** that LATER receives a *measured*
  `direct_observed_distribution` mapping is the only way to satisfy both the
  clean-holdout bar and the ≥20 direct-observed bar at once.
- **Does not count:** `assumption_labeled` (capped to a minority, excluded from
  the direct count), `action_anchor_only` / `evidence_only` / `reject` (cannot be
  ingested as a four-bucket observed case). The current buyer-only targets are
  `action_anchor_only` at best on the observed side — so they do **not** unblock
  Phase 15E even after their outcome arrives.

## Approval request (the one decision needed)

Everything is ready and the budget gate technically passes; I am holding at the
spend boundary because this is real money + an outward action. To proceed, the
human chooses targets and authorizes the spend:

- **Recommended top 3 to lock** (short horizon, clean metric, range of campaign
  sizes): `hollowed_oath_god_mode_ks_2026` (≈3-day horizon, real $150k stretch-goal
  binary), `tomo_endless_blue_onibi_ks_2026`, `war_of_the_dragon_wot_ks_2026`.
- **Spend:** ~$3–6 per run (≈$9–18 for three), hard-capped via
  `ASSEMBLY_COST_HARD_USD=12 ASSEMBLY_LIVE_DISCUSSION_BASE_CAP_USD=10`; well under $30.
- **Action on approval:** author a brief per chosen target, run the cost-capped
  prediction, verify durable artifacts, run `phase_15i … --print-only`, inspect,
  then append exactly one pending case per target (observed=None, holdout=true).

Reply with which targets to run (or "all three") to authorize the paid runs.
