"""Closed enums for the behavioral science mechanism library.

The migration mirrors these in DB CHECK constraints. Python validators
re-check them so failures surface earlier with structured violations.

Critical invariants:
  - `INFERENCE_STRENGTHS` deliberately EXCLUDES the value 'strong'. The
    strongest belief-network spillover the framework allows is 'moderate'.
    The DB CHECK constraint on `belief_network_rules` rejects 'strong'.
  - Mechanism priors NEVER outrank source evidence. The validator and
    initializer enforce this.
"""
from __future__ import annotations

from typing import Final


# -- Research source types -------------------------------------------------
SOURCE_TYPES: Final[tuple[str, ...]] = (
    "uploaded_paper",
    "peer_reviewed_paper",
    "preprint",
    "dataset_paper",
    "internal_note",
    "other",
)

# -- Mechanism categories --------------------------------------------------
MECHANISM_CATEGORIES: Final[tuple[str, ...]] = (
    "persuasion",
    "opinion_change",
    "conformity",
    "belief_network",
    "memory",
    "planning",
    "social_influence",
    "simulation_bias",
    "population_sampling",
    "argument_style",
    "evidence_processing",
)

# -- Mechanism status values -----------------------------------------------
MECHANISM_STATUSES: Final[tuple[str, ...]] = (
    "active",
    "experimental",
    "deprecated",
)

# -- Evidence link support types -------------------------------------------
EVIDENCE_SUPPORT_TYPES: Final[tuple[str, ...]] = (
    "direct_claim",
    "empirical_result",
    "theoretical_support",
    "caution_or_limitation",
    "implementation_inspiration",
)

# -- Belief network relation types -----------------------------------------
RELATION_TYPES: Final[tuple[str, ...]] = (
    "same_cluster",
    "adjacent_cluster",
    "unrelated",
    "conflict",
)

# -- Belief network inference strengths ------------------------------------
# 'strong' is DELIBERATELY EXCLUDED. The strongest spillover allowed is
# 'moderate'. Source evidence ALWAYS outranks belief priors.
INFERENCE_STRENGTHS: Final[tuple[str, ...]] = (
    "none",
    "weak",
    "moderate",
)
FORBIDDEN_INFERENCE_STRENGTHS: Final[tuple[str, ...]] = ("strong",)

# -- Persuasion strategy taxonomy (closed catalog) -------------------------
PERSUASION_STRATEGIES: Final[tuple[str, ...]] = (
    "logical_appeal",
    "emotional_appeal",
    "credibility_appeal",
    "personal_story",
    "self_modeling",
    "foot_in_the_door",
    "task_product_information",
    "source_related_inquiry",
    "task_related_inquiry",
    "personal_related_inquiry",
    "evidence_linking",
    "social_proof",
    "authority_signal",
    "peer_conformity_signal",
)

# -- Domain labels for applicability rules. Closed catalog --------------
APPLICABILITY_DOMAINS: Final[tuple[str, ...]] = (
    "commerce",
    "saas_tooling",
    "consumer_goods",
    "political_opinion",
    "health",
    "well_supported_topic",
    "unsupported_demographic_only",
    "low_evidence_domain",
)

# -- Anti-pattern marker reserved for the audit row's warnings array ----
ANTI_PATTERN_DEMOGRAPHIC_ONLY: Final[str] = (
    "demographic_only_roleplay_refused"
)
ANTI_PATTERN_PRIOR_OUTRANKED_EVIDENCE: Final[str] = (
    "mechanism_prior_attempted_to_outrank_source_evidence"
)
ANTI_PATTERN_FORBIDDEN_STRENGTH: Final[str] = (
    "belief_rule_strong_strength_forbidden"
)
