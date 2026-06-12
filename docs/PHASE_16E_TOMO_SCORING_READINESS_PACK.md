# Phase 16E — Tomo Scoring Readiness Pack

**Status:** READ-ONLY preparation. **No** scoring, **no** model change, **no** calibration, **no**
forecast/prompt change, **no** outcome added, **no** Tomo edit, **no** Phase 13 / token change, **no**
accuracy claim. This pack pre-registers exactly *how* Tomo will be scored **before** its outcome is
known — so the scoring is mechanical, anti-overfit, and identical in discipline to the Hollowed Oath
scoring. Scoring itself happens in a **later** phase, **on/after 2026-06-21**.

> Pre-committing the scoring rubric before seeing Tomo's numbers is deliberate: it prevents post-hoc
> rationalization of a hit/miss verdict.

---

## 1. Tomo lock summary (verified from the repo, read-only)

| Field | Value |
|---|---|
| Target | **Tomo: Endless Blue** (`tomo_endless_blue_onibi_ks_2026`) — voxel open-world RPG by Onibi |
| run_id | `4fcc4cbf-64d5-478f-a4a1-88df1a5c6ea9` |
| prediction_hash | `sha256:0a9ce639cf8ad2ae363fa46994036a8aa8afeb2c7d2dd96a49922a54263fa2e8` |
| locked_at | `2026-06-04T03:23:13Z` (before outcome) |
| **Locked prediction** | buyer/action-positive **0.0** · receptive **83.33** · uncertain/proof-needed **8.33** · skeptical/resistant **8.33** |
| Source type | kickstarter · Category | video_game_voxel_rpg |
| Outcome metric | final pledged USD + final backer count at close **2026-06-20** |
| Outcome check date | **on/after 2026-06-21** |
| Funding goal | $100,000 (pledge ≥ ~$24 for a digital copy) |
| Current status | **pending · observed = null** (must remain untouched until scored) |

Lock integrity (must still hold at scoring time): `prediction_hash` self-reproduces; 16A-P verifier
exit 0; observed null; used_for_holdout=true / used_for_training=false; action_signals empty (it is
still a blind pending lock until scored).

---

## 2. Source verification plan (check on/after 2026-06-21)

Direct `WebFetch` of kickstarter.com typically **403s** to automated tools — expect to corroborate via
trackers + press, exactly as Hollowed Oath was verified. Use **public** sources only:

1. **Kickstarter campaign page** (canonical): `https://www.kickstarter.com/projects/onibi/tomoendlessblue`
   — the authoritative final figure once closed (note: the Phase 16A shortlist had a **wrong** slug
   `/onibi/tomo-endless-blue`; the **correct** slug is `/onibi/tomoendlessblue`).
2. **Kicktraq**: `https://www.kicktraq.com/projects/onibi/tomoendlessblue/` (had only placeholder data
   pre-close; may carry the final tally after close).
3. **BackerKit / BackerTracker** if a campaign mirror exists (also records post-close late pledges
   separately — see §4 late-pledge policy).
4. **Press already used at lock verification:** RPGamer, MassivelyOP (2026-05/06 coverage). Search for a
   close-out / funding-result article.
5. **Public archive** (e.g. web.archive.org of the campaign page at/after close) **only if clearly
   cited**.

**Required fields to record (≥2 independent corroborating sources, no conflict):**
- final pledged amount (USD) · final backer count · campaign close date (expected 2026-06-20)
- percentage of the $100,000 goal · base-goal status (funded / not funded)
- stretch-goal status if any are visible/inferable
- **late-pledge caveat** (was BackerKit/late pledging open after close? — recorded separately, see §4)
- explicit note of any source uncertainty or conflict.

---

## 3. Date gate (hard preconditions — STOP if any fails)

- **Do NOT score before 2026-06-21.** Today's date must be **on/after 2026-06-21** (the campaign closes
  2026-06-20; final numbers settle after close).
- **STOP** if the final pledged/backer numbers are **not publicly final** (only mid-campaign / "N days
  left" snapshots visible).
- **STOP** if sources **conflict** on the final figures.
- **STOP** if the campaign was **relaunched / cancelled / extended** such that "close at 2026-06-20" is
  no longer the right metric → route to INCONCLUSIVE (§5).
- **STOP** if no Tomo outcome already exists in the repo *and* you cannot satisfy the above — i.e., never
  invent or estimate numbers.

*(Precedent: the first two Hollowed Oath scoring attempts correctly STOPPED on the date gate; the third,
on 2026-06-04 with public final numbers, proceeded.)*

---

## 4. Scoring type (identical convention to Hollowed Oath — Phase 16B-R)

If only Kickstarter buyer/action data is available (the expected case), score Tomo via the **Phase 16B-R
prospective partial-outcome convention** (`docs/PHASE_16B_PROSPECTIVE_PARTIAL_OUTCOME_CONVENTION.md`):

- **mapping_type:** `action_anchor_only` · **scoring_type:** `partial_buyer_anchor`
- **`observed` remains NULL** — do **not** populate receptive/uncertain/skeptical; do **not** compute a
  four-bucket MAE; do **not** invent any bucket.
- **`validation_status`:** pending → **partial**
- **buyer evidence → `action_signals`**: one `ActionSignal` — `signal_type: kickstarter_pledge`
  (auto-Tier-1), `source_type: kickstarter`, `count: <final backers>`, `denominator: null`,
  `direction: positive|negative`, `confidence: medium`, `observed_at: 2026-06-20`,
  `source_reference: <urls>`, notes (pledged $, goal %, stretch status, late-pledge caveat, "non-buyer
  buckets unobservable").
- **Separate audit record:** `validation_cases/prospective_outcomes/run_4fcc4cbf-…json`
  (`purpose: prospective_partial_outcome_not_observed_distribution`, absent from manifest), with the full
  machine-readable outcome + directional verdict + the §5 reasoning.
- **Lock untouched:** `predicted` / `prediction_lock` / `prediction_hash` unchanged; HO untouched.
- **NOT** a `direct_observed_distribution`; **does NOT** unlock Phase 15E.
- **Verify** with `scripts/phase_16b_verify_prospective_partial_outcome.py` (and re-run the 16A-P verifier
  for Tomo + Hollowed) → all exit 0 before commit.

**Late-pledge policy:** use the **Kickstarter-close** numbers for the locked score. Record any BackerKit /
post-close late pledges **separately as a caveat** — they must **not** alter the locked close score (no
repo policy permits late-pledge inclusion).

---

## 5. Hit / miss / inconclusive criteria

Locked `buyer_action_positive = 0.0` (Tomo predicted ~0% would take the buy/back action). This is a
**directional buyer-anchor** judgment (not a four-bucket comparison). Apply the **same standard used for
Hollowed Oath** (which funded 166% / 698 backers → MISS):

### MISS — locked 0.0 is contradicted (material buyer/action demand)
- The campaign shows **material** buyer/action: e.g. **funded** (≥100% of the $100k goal) **and/or**
  several-hundred-plus paying backers, and/or strong stretch-goal progress.
- Rationale: a campaign that converts hundreds of paying backers had buyer/action demand materially above
  the predicted ~0%.

### HIT — locked 0.0 is supported (weak/absent buyer/action demand)
- The campaign **fails to fund** (well under the $100k goal at close) with a **low** backer count, or
  otherwise materially fails to convert interest into paid backing.
- Rationale: near-zero realized buyer/action demand is what the lock predicted.

### INCONCLUSIVE — cannot cleanly judge the buyer direction
- Final numbers unavailable / not yet public.
- Sources conflict on the final figures.
- Late pledging obscures the close result (close figure not separable).
- Campaign relaunch / cancellation / extension makes the "close at 2026-06-20" metric ambiguous.

> Borderline (e.g. partially funded, mid-hundreds of backers but under goal) is a **judgment call**:
> record the figures, state the reasoning explicitly in the outcome record, and lean **INCONCLUSIVE**
> rather than forcing a verdict. Do **not** retro-fit the threshold to the result.

---

## 6. Post-Tomo decision tree

### If Tomo is ALSO a strong buyer-anchor MISS
- Classify as a **candidate 2-case repeated buyer-underprediction pattern** (Hollowed + Tomo).
- **Do NOT auto-tune.** Open a **scoped diagnostic phase** (per `docs/PHASE_16D_…`).
- Compare Hollowed vs Tomo **evidence, ballots, and category/source dynamics**: were buyer-positive
  voters *suppressed* or *absent*? should receptive voters have been buyer under a crowdfunding reading?
- **Investigate the crowdfunding/action-prior hypothesis (H1)** specifically; rule out leakage /
  source / brief defects.
- **Require a held-out validation plan** (predict-before-outcome on NEW cases) before *any* fix; a
  *global* change additionally needs **non-Kickstarter** corroboration.

### If Tomo is a HIT
- **Hollowed remains isolated / uncertain.** No model change. No calibration.
- Continue prospective locks with **better target diversity**.

### If Tomo is INCONCLUSIVE
- **No model change.** Hold the no-change posture.
- Prioritize a **non-Kickstarter** target next (State of CSS 2026 — possible direct-observed; or Slate
  Auto — Tier-1 conversion) to get a different source/denominator before any pattern claim.

---

## 7. Safety rules (explicitly forbidden)

- ❌ Scoring **before** final numbers are public (date gate / numbers gate).
- ❌ Manually increasing buyer probabilities anywhere.
- ❌ **Adding a crowdfunding prior now** (it is a *hypothesis*; it requires ≥2 scored misses + a held-out
  validation plan — neither exists yet).
- ❌ Changing bucket definitions or routing.
- ❌ Using Tomo's outcome to retroactively alter any locked prediction (Tomo's or Hollowed's).
- ❌ Treating `action_anchor_only` as `direct_observed_distribution`, or counting it toward Phase 15E.
- ❌ Touching the Phase 15E gates.
- ❌ Scoring Tomo before 2026-06-21, or editing the lock / artifacts.

---

## 8. Checks (this phase)

Docs-only — no full test suite required. Read-only sanity at authoring time confirmed:
`git diff --name-only` shows only this doc; `load_all_cases()` == 8 (6 training / 2 holdout / 2 clean
holdout / 1 partial / 1 pending); the 16A-P verifier passes for Tomo + Hollowed and the 16B-R verifier
passes for Hollowed; Phase 15E blocked. No paid-provider or runtime LLM calls.

---

## Summary

Everything needed to score Tomo **safely, mechanically, and anti-overfit** is pre-registered here: the
verified lock facts, the public-source plan, the hard date/numbers gates, the exact `action_anchor_only`
recording convention (observed stays null), pre-committed hit/miss/inconclusive rules, and the post-Tomo
decision tree. **Tomo is not scored** by this phase; the model and ledger are unchanged. The next true
evidence point is the Tomo outcome **on/after 2026-06-21**.
