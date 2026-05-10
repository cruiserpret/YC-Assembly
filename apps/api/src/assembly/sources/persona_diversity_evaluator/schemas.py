"""Phase 8.5D.1C — schemas for the persona-diversity evaluator."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


DiversityRecommendation = Literal[
    "READY",                  # diverse enough → mutating phase OK
    "DEFER_DIVERSIFY",        # 2+ roles but skewed → improve before persisting
    "DEFER_SOURCE_COVERAGE",  # 1 role only → broaden source first
    "DEFER_NO_CANDIDATES",    # zero candidates → cannot persist
]


class PersonaDiversityEvaluation(BaseModel):
    """Per-run diversity audit. Pure-data shape, no DB references."""

    model_config = ConfigDict(extra="forbid")

    diversity_score: float  # 0.0 (collapse) .. 1.0 (max diversity)
    primary_role_count: int  # total candidate count
    unique_primary_roles: list[str]
    unique_secondary_roles: list[str]
    evidence_source_count: int  # distinct source_record_ids cited
    competitor_concentration: float  # 0..1 (1.0 = all candidates from one competitor)
    duplicate_role_cluster_count: int  # clusters with >=2 same-primary-role
    persona_similarity_warnings: list[str]
    undercovered_evidence_themes: list[str]
    mutating_persistence_recommendation: DiversityRecommendation
    narrow_source_proof_only: bool
    rationale: list[str]
