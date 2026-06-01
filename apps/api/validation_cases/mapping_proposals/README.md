# Mapping proposals — human-supplied PROPOSALS, never validation data

This directory holds **proposed outcome mappings** (Phase 15L-B): a human
reviewer's draft mapping of a candidate's public evidence into Assembly's four
buckets (`buyer_action_positive`, `receptive`, `uncertain_proof_needed`,
`skeptical_resistant`).

**These files are NEVER loaded as validation cases.** Like `candidates/` and
`acquisition_backlog.json`, this directory is **deliberately absent from
`manifest.json`**, so `loader.load_all_cases()` never reads it. Each proposal
carries `purpose: "mapping_proposal_not_validation_data"` and
`human_approved: false`.

## What a proposal is — and is not

A proposal is a **draft**, not an approval and not an observed outcome. It does
not change any candidate, does not fill `claimed_outcome_proportions` on the
candidate JSON, does not ingest anything, and does not unlock calibration.

## The five mapping types (Phase 15L-B protocol)

| type | proportions | when |
|---|---|---|
| `direct_observed_distribution` | all four **observed** over a real census / representative sample | the only type that counts toward the ≥20 readiness bar — **0/8 current candidates qualify** |
| `assumption_labeled_distribution` | buyer anchor observed; non-buyer buckets are **explicitly-labeled imported priors** | training-eligible only as down-weighted, low-confidence, capped to a minority |
| `action_anchor_only` | **null** — only the buyer/action numerator is recorded | the maximal honest mapping for a self-selected buyer count |
| `evidence_only` | **null** — anchor too soft/cumulative/estimate-based | retains qualitative signal only |
| `reject` | **null** | non-credible / unverifiable / would require inventing buckets |

The binding rule: a buyer NUMERATOR over a self-selected denominator can never,
by itself, imply the three non-buyer buckets — they are mathematically
unidentified and may only be **measured** or **imported as labeled assumptions**,
never derived.

## How to use (all read-only / dry-run)

```bash
cd apps/api
export PYTHONPATH=src
# 1. classify a candidate's maximal-honest type
python scripts/phase_15l_mapping_protocol.py classify --candidate-id <id>
# 2. emit a blank proposal template to fill
python scripts/phase_15l_mapping_protocol.py mapping-template --candidate-id <id> \
    --out validation_cases/mapping_proposals/<id>.json --dry-run
# 3. validate a filled proposal against the hard gates
python scripts/phase_15l_mapping_protocol.py validate-mapping --from validation_cases/mapping_proposals/<id>.json
# 4. mapping-quality-aware Phase 15E readiness
python scripts/phase_15l_mapping_protocol.py dashboard
```

A proposal that passes `validate-mapping` is still **only a proposal** — actual
promotion requires the Phase 15J factory `reviewer_checklist` to be completed and
the promotion gates to pass (`scripts/phase_15j_candidate_factory.py`). See
`docs/PHASE_15L_B_OBSERVED_OUTCOME_MAPPING_PROTOCOL.md`.

`TEMPLATE.json` is a blank scaffold (placeholders — not itself schema-valid).
`EXAMPLE_action_anchor_only_exploding_kittens.json` is a valid, gate-passing
example of the maximal-honest mapping for a current candidate.
