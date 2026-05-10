"""Phase 9D — universal persona feature vector for clustering.

Builds a fixed-shape numeric feature vector per persona using:
  1. role / context (one-hot over present roles)
  2. evidence (provider one-hot, evidence_theme bucket)
  3. psychology (11 OCEAN + additional traits as floats in [0, 1])
  4. discussion behavior (pre / final stance one-hots, public_private
     delta one-hot, peer reference count, has_objection, has_proof_need)
  5. memory signal (count of memory atoms by type)

Forbidden features (asserted in tests):
  race, ethnicity, religion, political affiliation, sexual_orientation,
  mental_health, income, household_income, immigration, citizenship.
"""
from __future__ import annotations

from typing import Any

# Closed-set vocabulary — kept here so the clusterer + tests can verify
# every feature dimension. Universal across products.
PSYCHOLOGY_TRAIT_NAMES: tuple[str, ...] = (
    "openness", "conscientiousness", "extraversion",
    "agreeableness", "neuroticism", "risk_tolerance",
    "novelty_seeking", "trust_proof_threshold",
    "social_influence_susceptibility",
    "category_involvement_or_expertise", "price_sensitivity",
)
ALLOWED_STANCES: tuple[str, ...] = (
    "curious_but_unconvinced", "interested_if_proven",
    "skeptical", "likely_reject", "needs_more_information",
)
PUBLIC_PRIVATE_DELTAS: tuple[str, ...] = (
    "private_acceptance", "public_compliance_only",
    "resistance", "no_change", "polarization",
    "uncertainty_increase",
)
MEMORY_TYPES_FEATURE_AXIS: tuple[str, ...] = (
    "trait", "psychology", "evidence", "prior_simulation",
    "discussion_turn", "private_ballot",
)

# Forbidden feature names — sensitive-inference guard.
FORBIDDEN_FEATURE_NAMES: frozenset[str] = frozenset({
    "race", "ethnicity", "religion", "religious", "political",
    "party_affiliation", "sexual_orientation", "lgbt", "trans",
    "mental_health", "depression", "anxiety_disorder", "ptsd",
    "schizophrenia", "bipolar", "diagnosis", "diagnosed",
    "medical_condition", "disability", "income", "household_income",
    "net_worth", "credit_score", "immigration", "citizenship", "ssn",
})


def _onehot(value: str | None, vocabulary: tuple[str, ...]) -> dict[str, float]:
    return {f"oh::{v}": 1.0 if value == v else 0.0 for v in vocabulary}


def build_cohort_feature_vectors(
    *,
    personas: list[dict[str, Any]],
    role_vocabulary: tuple[str, ...] | None = None,
    provider_vocabulary: tuple[str, ...] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Return (per-persona dense feature dicts, metadata dict).

    Required dict keys per persona:
      persona_id, normalized_primary_role, source_provider_family,
      psychology_value_map (dict[trait_name -> float in [0,1]]),
      pre_stance, final_stance, public_private_delta,
      peer_reference_count, has_top_objection, has_top_proof_need,
      memory_atom_count_by_type (dict[memory_type -> int]),
      reflection_present (bool).
    """
    role_vocab = role_vocabulary or tuple(sorted({
        p.get("normalized_primary_role") or "unknown" for p in personas
    }))
    provider_vocab = provider_vocabulary or tuple(sorted({
        p.get("source_provider_family") or "unknown" for p in personas
    }))
    feature_names: list[str] = []

    out_vectors: list[dict[str, float]] = []
    for p in personas:
        vec: dict[str, float] = {}
        # Psychology numeric (11 dims)
        psy = p.get("psychology_value_map") or {}
        for tname in PSYCHOLOGY_TRAIT_NAMES:
            v = float(psy.get(tname, 0.5))
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            vec[f"psy::{tname}"] = v
        # Role one-hot
        vec.update({
            f"role::{k.split('::')[-1]}": v
            for k, v in _onehot(
                p.get("normalized_primary_role"), role_vocab,
            ).items()
        })
        # Provider one-hot
        vec.update({
            f"provider::{k.split('::')[-1]}": v
            for k, v in _onehot(
                p.get("source_provider_family"), provider_vocab,
            ).items()
        })
        # Stance one-hots
        vec.update({
            f"pre_stance::{k.split('::')[-1]}": v
            for k, v in _onehot(p.get("pre_stance"), ALLOWED_STANCES).items()
        })
        vec.update({
            f"final_stance::{k.split('::')[-1]}": v
            for k, v in _onehot(p.get("final_stance"), ALLOWED_STANCES).items()
        })
        # Public/private delta
        vec.update({
            f"delta::{k.split('::')[-1]}": v
            for k, v in _onehot(
                p.get("public_private_delta"), PUBLIC_PRIVATE_DELTAS,
            ).items()
        })
        # Numeric flags
        vec["peer_ref_count_norm"] = min(
            1.0, float(p.get("peer_reference_count", 0)) / 4.0,
        )
        vec["has_top_objection"] = (
            1.0 if p.get("has_top_objection") else 0.0
        )
        vec["has_top_proof_need"] = (
            1.0 if p.get("has_top_proof_need") else 0.0
        )
        vec["reflection_present"] = (
            1.0 if p.get("reflection_present") else 0.0
        )
        # Memory atom counts by type (normalized: log + cap)
        atoms = p.get("memory_atom_count_by_type") or {}
        for mt in MEMORY_TYPES_FEATURE_AXIS:
            n = int(atoms.get(mt, 0))
            vec[f"memory::{mt}"] = min(1.0, n / 6.0)
        out_vectors.append(vec)
        if not feature_names:
            feature_names = sorted(vec.keys())
    return out_vectors, {
        "feature_names": feature_names,
        "feature_dim": len(feature_names),
        "role_vocabulary": list(role_vocab),
        "provider_vocabulary": list(provider_vocab),
        "stance_vocabulary": list(ALLOWED_STANCES),
        "delta_vocabulary": list(PUBLIC_PRIVATE_DELTAS),
        "psychology_axis": list(PSYCHOLOGY_TRAIT_NAMES),
        "memory_axis": list(MEMORY_TYPES_FEATURE_AXIS),
        "forbidden_feature_names": sorted(FORBIDDEN_FEATURE_NAMES),
    }
