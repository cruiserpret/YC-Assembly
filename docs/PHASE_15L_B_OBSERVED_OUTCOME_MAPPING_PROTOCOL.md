# Phase 15L-B — Observed Outcome Mapping Protocol

**Status: protocol + validators + CLI + tests only. NOTHING ingested, NO candidate
approved, NO proportion invented, NO calibration, NO forecast change. Official
ledger unchanged (6 training / 0 holdout / 0 pending). Phase 15E remains BLOCKED.**

This protocol decides **when partial public outcome evidence may become a valid
Assembly _observed_ outcome** (training-eligible), **when it must stay
evidence-only**, and **when it must be rejected** — without ever inventing the
three non-buyer buckets.

It is implemented as a NEW, pure, isolated module that **composes** the existing
factory/ledger public APIs and edits none of them:

- `apps/api/src/assembly/validation_factory/outcome_mapping_protocol.py` — schema (`ProposedOutcomeMapping`, `BucketMappingRationale`, `MappingValidationResult`, `OutcomeMappingProtocol`), the classifier, the gates, and the readiness report.
- `apps/api/scripts/phase_15l_mapping_protocol.py` — read-only / dry-run CLI.
- `apps/api/validation_cases/mapping_proposals/` — isolated proposal store (absent from `manifest.json`, never loaded), with `README.md`, `TEMPLATE.json`, and a valid `EXAMPLE`.
- `apps/api/tests/test_outcome_mapping_protocol_15l_b.py` — gate + isolation tests.

## The binding finding it enforces

Every one of the 8 current candidates supplies only a **buyer/action-positive
NUMERATOR** (backers, downloads, forks, units) over a **self-selected
denominator**. A buyer numerator over a self-selected denominator can **never, by
itself, imply** the receptive / uncertain_proof_needed / skeptical_resistant
proportions, for three independent reasons:

1. **Missing / biased denominator.** The count of people who acted is a numerator
   whose denominator — the full addressable market that was exposed and could
   have acted — is unmeasured and self-selected (and for cumulative stars/forks,
   non-existent). Even the buyer _share_ is undefined.
2. **The non-actors are never observed.** The three non-buyer buckets describe
   people who did **not** act. A buyer count carries zero bits about how the
   non-actors split across receptive vs uncertain vs skeptical; any split summing
   to the remainder is equally consistent with the evidence — they are
   mathematically **unidentified**.
3. **Selection bias poisons even the anchor.** Captive/creator fanbases, extreme
   self-selection, and free-action curiosity inflate the count, so a high action
   count in a captive crowd says little about a cold market.

Therefore the maximal honest mapping from a self-selected buyer numerator is
`action_anchor_only`. A four-bucket distribution requires **either** a true
representative measurement (`direct_observed_distribution`) **or**
explicitly-labeled imported assumptions (`assumption_labeled_distribution`).
Filling the three buckets from the numerator alone is **fabrication**.

## Mapping types

| type | `proposed_proportions` | provenance | training? | counts toward ≥20 bar? |
|---|---|---|---|---|
| **`direct_observed_distribution`** | all four **observed** over a fixed census / representative random sample | `measured_four_bucket` | yes | **yes (weight 1.0)** |
| **`assumption_labeled_distribution`** | buyer anchor observed; three non-buyer buckets = **explicitly-labeled imported priors** | `assumption_based_labeled` | yes, **down-weighted, low-confidence, capped** | **no (weight 0.0)** |
| **`action_anchor_only`** | **null** (anchor only) | `buyer_anchor_only` | no | no |
| **`evidence_only`** | **null** | `none` | no | no |
| **`reject`** | **null** (+ mandatory reason) | `none` | no | no |

- **`direct_observed_distribution`** — all four buckets independently measured
  over ONE common externally-fixed denominator (the addressable market or a
  documented unbiased random sample). The three non-buyer buckets are observed,
  never `100 − buyer`. **0/8 candidates qualify.**
- **`assumption_labeled_distribution`** — a defensible observed anchor plus the
  three non-buyer buckets produced by the reviewer applying **explicitly stated,
  documented priors**. It is honestly labeled as assumption-based; it is
  TRAINING-eligible only as a down-weighted, low-confidence, flagged case, and is
  **capped to a minority** (≤ ⌊n_direct/3⌋). It is **never** holdout (all
  retrospective) and can **never be the marginal reason** any readiness threshold
  is met.
- **`action_anchor_only`** — records ONLY the buyer/action anchor (count,
  direction, source, denominator quality); the other three buckets stay null. The
  natural resting state for a self-selected buyer count. Counts toward the
  Phase 15E **Tier-1/2 action-outcome** requirement (via its real signal) but
  **0.0** toward the ≥20 distribution bar.
- **`evidence_only`** — the anchor is too soft / cumulative / estimate-based to
  trust even as a clean anchor (free installs, cumulative free forks/stars with
  no denominator). Nothing numeric is mapped; qualitative/directional signal is
  retained.
- **`reject`** — non-credible, unverifiable, fabricated, or admitting it would
  require inventing buckets. Sets `status=rejected` with a mandatory reason.

## Mapping confidence

- **high** — reserved for a `direct_observed_distribution` (or to tag the
  primary/audited _anchor_ of an `action_anchor_only` case). **Never** attaches to
  an assumption-derived non-buyer bucket.
- **medium** — a credibly-cited anchor with a known bias/caveat (captive fanbase,
  repeat backers, a medium-confidence secondary count). The ceiling for an
  assumption-labeled case overall.
- **low** — soft / third-party / press-estimate anchors, and **every**
  non-buyer bucket value in an assumption-labeled mapping.

## Denominator quality

Only the first two support a measured four-bucket distribution:

`fixed_external_census` · `representative_random_sample` ·
`self_selected_funnel_counted` · `self_selected_funnel_estimated` ·
`no_denominator_cumulative` · `unknown`.

A proportion is numerator/denominator: with an unknown or self-selected
denominator you can assert a buyer **count**, not a market **share**, and you can
never observe the non-buyer split.

## The hard gates (enforced by `validate_mapping`)

| gate | blocks |
|---|---|
| **G1** denominator known + typed | `direct_observed` without a positive, typed, representative denominator |
| **G2** self-selected sample | non-buyer bucket mass from a self-selected sample without explicit assumption labeling |
| **G3** buyer-numerator-only | a single anchor promoted as a full distribution |
| **G4** free action ≠ buyer | a free download/install/star/fork anchoring a buyer percentage without an explicit `free_action_weak_proxy` label (→ forced low confidence) |
| **G5** fulfillment failure ≠ skepticism | undelivered backers coded as `skeptical_resistant` (within-buyer, not market skeptics) |
| **G6** returns/churn ≠ skepticism | returners coded as non-buyer skeptics / double-counted |
| **G7** retrospective ≠ clean holdout | a known-outcome retrospective designated holdout (reuses the ledger anti-leakage validators) |
| **G8** anchor masquerade | a buyer anchor padded to sum-100 and presented as measured (per-bucket `observed` basis + citation required) |
| **G9** sum-to-100 | structurally enforced by `MarketDistribution` (±1.5pp); **necessary, not sufficient** — a fabrication can sum to 100 |
| **G10** calibration needs approval | a proposal is never calibration-ready; `human_approved` is pinned `False` |
| **G11** estimate confidence floor | estimate-quality counts entering above `low` confidence (→ downgraded/flagged) |
| **G12** concentration cap | (readiness) Kickstarter > cap, > 1 same-company entry, source/category dominance |

**Two confusions the protocol exists to prevent:** (a) "it sums to 100 and passes
pydantic" ≠ "it is real" — G9 is structural only; realness comes from G1/G2/G3/G8.
(b) within-buyer fulfillment/returns splits (Coolest Cooler's >32% unfulfilled,
Humane's returns>sales) are **dissatisfied buyers**, never the non-buyer skeptical
bucket — G5/G6.

## When a candidate can become a training case

A candidate may become a TRAINING case only when a human supplies a proposed
mapping that passes the gates as **either**:

- a `direct_observed_distribution` (all four buckets measured over a real
  denominator — none of the current 8 can be), **or**
- an `assumption_labeled_distribution` with every required assumption written
  down, the non-buyer buckets labeled `assumption`, the `assumption_based_mapping`
  flag set, confidence forced to `low`, and within-buyer splits kept out of the
  non-buyer buckets — and even then only as a **down-weighted, capped** case,

**and** the Phase 15J factory `reviewer_checklist` is completed and the promotion
gates pass. This protocol validates the mapping; it does **not** itself approve or
ingest anything.

## Why retrospectives can never be a clean holdout

A clean holdout requires a prediction **locked before** the outcome
(`is_clean_holdout` / `validate_no_outcome_leakage`). All 8 candidates are
retrospective known outcomes, so 0/8 can be holdout. A clean holdout can only come
from the **prospective** path: lock an Assembly prediction before the outcome
(Phase 14C durable artifacts + 15I prediction-lock bridge), then record the
outcome.

## Phase 15E readiness (mapping-quality-aware)

`mapping_readiness` replaces the weak "every ingested case counts toward 20" rule
with:

```
phase_15e_ready =
    n_direct_observed_distribution_cases >= 20      # assumption/anchor/evidence-only do NOT count
    AND n_clean_holdout >= 1                         # prospective only
    AND n_tier1_2_action_outcome_cases >= 1
    AND n_assumption_labeled <= floor(n_direct / 3)  # minority cap
    AND weak_mapping_warning == false
    AND source/entity concentration within caps
    AND explicit_human_signoff == true
```

`weak_mapping_warning` fires when the case count would reach the target only via
non-direct mappings, when assumption-labeled cases exceed the cap, when a
non-independent entity cluster exists (e.g. the two Pebbles = same company), or
when a single source/category dominates. **The warning is non-overridable for
unlock purposes**: only adding genuine direct-observed cases clears it.

**Current value:** `n_direct = 0`, so Phase 15E is **BLOCKED** regardless of the
other terms — even if a reviewer assumption-labels all 8, `n_direct` stays 0.

## Candidate classification today

| candidate | maximal-honest type |
|---|---|
| pebble_time, pebble_original, exploding_kittens, coolest_cooler, vox_machina, humane | **`action_anchor_only`** (solid paid/counted anchor over a self-selected funnel) |
| clubhouse (free installs, estimates), automatic1111 (cumulative free forks/stars, no denominator) | **`evidence_only`** |

**0 `direct_observed`, 0 `assumption_labeled` (until a human supplies one),
0 `reject`.** No candidate JSON was modified; `claimed_outcome_proportions` stays
null on all 8.

## Recommended follow-up (NOT done here — would change the ingest path)

The 15J promotion path currently hardcodes `denominator_type="unknown"` and
`observation_confidence="medium"` and never wires `observed_at`. Wiring the
protocol's gates (G1 denominator, G11 confidence floor, G7 `observed_at`, G8
provenance) **into** `evaluate_promotion_gates` / `build_case_payload_from_candidate`
is a sensible **Phase 15L-C** hardening — but it changes the factory ingest
behavior and is deliberately out of scope here, where the official ledger and the
15J tests must stay untouched. Until then the protocol is an enforced,
testable guardrail a reviewer runs **before** approving; the factory already
refuses to promote the 8 null-proportion candidates, so nothing can sneak in.
