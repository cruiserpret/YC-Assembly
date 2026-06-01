# Phase 15J — Validation Case Factory

## What this is

An operational, repeatable, auditable system for turning **real external
market-outcome leads** into trustworthy validation-ledger cases. It exists to
grow the ledger toward the Phase 15E unlock bar — **20+ diverse reviewed cases,
a clean holdout, and Tier-1/Tier-2 action outcomes** — without ever letting weak,
duplicated, leaky, or fabricated data into calibration.

It improves *how we gather validation data*. It is **not** a model change.

## What this is NOT

- It does **not** change any forecast, apply any calibration, or claim improved
  accuracy.
- It does **not** invent cases. The factory only ingests data a human supplies
  and a human reviews.
- It does **not** touch the frozen `seed_cases.json` (the 6-case baseline,
  avg MAE 25.30), Phase 13, the token system, or `config.py`.
- A **candidate is not a validation case** (see *Isolation*).

## The pipeline

```
capture ──> review ──> classify ──> promote(approve) ──> ingest
candidate   needs_review/  evidence    approved_for_*       append to the
            rejected       tier         (gates pass)         ledger split file
```

1. **Capture.** A candidate is a `CandidateCase` (see
   `src/assembly/validation_factory/candidate_schema.py`) created from a JSON /
   YAML / Markdown file. It records the lead, the cited sources, the *claimed*
   four-bucket outcome, the raw evidence, and candidate action signals. It starts
   `status="candidate"`.
2. **Review.** A human fills the `ReviewerChecklist` (forced yes/no questions +
   a designation + an evidence tier). Incomplete reviews block promotion.
3. **Classify.** The reviewer assigns an `evidence_tier` (1–4, reusing the
   Phase 15C taxonomy) that the candidate's action signals must actually support.
4. **Approve.** `approve --target {pending,training,holdout}` runs every hard
   gate; on success the candidate becomes `approved_for_<target>`.
5. **Ingest.** `ingest` builds a `ValidationCase` and appends it to the matching
   split file — `holdout_cases.json` / `training_cases.json` / `pending_cases.json`
   (never `seed_cases.json`). Every write supports `--dry-run`.

## Evidence tiers (reused from Phase 15C, not redefined)

| Tier | Meaning | Examples |
|------|---------|----------|
| 1 | revealed action | purchase, paid signup, backer pledge, trial conversion, install, github_fork |
| 2 | semi-action | github_star, PH upvote, waitlist signup, traffic |
| 3 | public opinion | comment sentiment, praise/criticism, reviews |
| 4 | synthetic | deep-agent / 100-voter / behavioral forecast (the thing being validated — never independent evidence) |

**Tier-3/Tier-4 evidence is never treated as equivalent to Tier-1/Tier-2.** The
factory enforces this and only counts Tier-1/Tier-2 toward the Phase 15E unlock.

## The hard gates (all enforced by `evaluate_promotion_gates`)

1. **Required fields + ≥1 source_url** — no anonymous, source-less cases.
2. **Reviewer checklist complete** — every required question answered, a
   designation chosen, and (unless rejecting) an evidence tier assigned.
3. **Anti-masquerade** — the assigned `evidence_tier` may not claim *stronger*
   evidence than the candidate's best action signal supports. A Tier-4 forecast
   can never be graded Tier-1.
4. **Per-signal tier consistency** — known signal types use the canonical tier;
   an unknown signal type may not self-declare a Tier-1/2 (action) tier.
5. **No phantom action evidence** — a Tier-1/2 signal must carry a
   `source_reference` and a positive `count`.
6. **No critical uncertainty flags** — any flag prefixed `critical:` blocks.
7. **Duplicate detection** — a deterministic `sha256` fingerprint (normalized
   name + date + category + source + primary URL + observed hash) plus a
   `(name, date, source)` composite key, checked against other candidates **and**
   the live ledger. Blocked by default; `--allow-duplicate` overrides explicitly.
8. **Observed-outcome discipline** — a `pending` case must carry **no** observed
   outcome; `training`/`holdout` require a reviewer-mapped four-bucket outcome.
9. **Clean-holdout anti-leakage** — a case with a *known* outcome and **no
   prediction locked before it** can never be a clean holdout (it reuses the
   ledger's own `validate_no_outcome_leakage` / `is_clean_holdout`). Retrospective
   cases belong in **training**, or are staged **pending** until an Assembly
   prediction is locked first.

## Isolation (a candidate is not a case)

- Candidates live in `validation_cases/candidates/` (one JSON per candidate),
  **deliberately absent from `manifest.json`** — so `load_cases` / `load_all_cases`
  never read them.
- `CandidateCase` is `extra="forbid"` and carries
  `purpose="candidate_evaluation_not_validation_data"`. A candidate JSON that
  smuggles in a ledger-only field (`observed` / `predicted` / `anti_overfit` /
  `metrics`) fails validation.
- Tests prove candidates are never merged into the ledger and that candidate ids
  are disjoint from case ids.

## How to use it

```bash
cd apps/api

# 1. Copy the template and fill REAL, cited data
cp validation_cases/templates/validation_candidate_template.json /tmp/mycase.json
# ... edit /tmp/mycase.json ...

# 2. Create (dry-run first)
python scripts/phase_15j_candidate_factory.py create --from /tmp/mycase.json --dry-run
python scripts/phase_15j_candidate_factory.py create --from /tmp/mycase.json

# 3. Validate / inspect
python scripts/phase_15j_candidate_factory.py validate --id <candidate_id>
python scripts/phase_15j_candidate_factory.py show --id <candidate_id>

# 4. (Edit the candidate's reviewer_checklist + evidence_tier, then) approve
python scripts/phase_15j_candidate_factory.py approve --id <candidate_id> --target training --dry-run
python scripts/phase_15j_candidate_factory.py approve --id <candidate_id> --target training

# 5. Ingest into the ledger (dry-run shows the exact case first)
python scripts/phase_15j_candidate_factory.py ingest --id <candidate_id> --dry-run
python scripts/phase_15j_candidate_factory.py ingest --id <candidate_id>

# Reject / defer
python scripts/phase_15j_candidate_factory.py reject --id <candidate_id> --reason "source not credible"
python scripts/phase_15j_candidate_factory.py needs-review --id <candidate_id>

# Readiness dashboard
python scripts/phase_15j_candidate_factory.py dashboard
```

## What NOT to do

- Do **not** add fake or speculative cases. Every candidate needs cited,
  externally observable sources.
- Do **not** scrape private or unauthorized data.
- Do **not** hand-append to `validation_cases/*.json` to bypass the review gates.
- Do **not** promote retrospective (known-outcome) cases to **holdout** — that is
  leakage. They are training data; a clean holdout requires a prediction locked
  *before* the outcome.
- Do **not** treat Tier-3/Tier-4 evidence as Tier-1/Tier-2.

## Why Phase 15E is still blocked

Phase 15E (the flag-gated calibrated prototype) remains **blocked** until the
**live ledger** has:

1. **≥20 diverse reviewed cases** (currently 6 training, 0 holdout),
2. **≥1 clean holdout case** (a prediction locked before its outcome), and
3. **≥1 case with Tier-1/Tier-2 action outcomes** (currently 0).

The factory is the machine that closes this gap honestly. The `dashboard`
subcommand reports current progress against each requirement. Until all three are
met, no calibration is fit and no forecast is changed.
