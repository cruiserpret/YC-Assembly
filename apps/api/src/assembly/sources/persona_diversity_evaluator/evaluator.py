"""Phase 8.5D.1C — deterministic persona-diversity evaluator.

`evaluate_persona_diversity(brief, candidates, plan)` returns a
`PersonaDiversityEvaluation` artifact. Pure function. No LLM, no
network. Same inputs → same output.
"""
from __future__ import annotations

from collections import Counter

from assembly.sources.evidence_anchor_planner import (
    EvidenceAnchorPlan, ProductBriefForPlanning,
)
from assembly.sources.persona_diversity_evaluator.schemas import (
    DiversityRecommendation, PersonaDiversityEvaluation,
)
from assembly.sources.persona_role_planner import PersonaCandidate


# Diversity thresholds — universal across products. The thresholds
# are deliberately strict so the evaluator can refuse to mark a
# narrow set ready for persistence.
_DIVERSITY_SCORE_READY_THRESHOLD = 0.5
_COMPETITOR_CONCENTRATION_WARNING = 0.6
_COMPETITOR_CONCENTRATION_HARD = 0.85
_MIN_UNIQUE_ROLES_READY = 2


def _competitor_key(candidate: PersonaCandidate) -> str:
    """Extract the candidate's `competitor_user_<x>` role if any.
    Returns 'none' if the candidate has no competitor role."""
    if candidate.inferred_persona_role.startswith("competitor_user_"):
        return candidate.inferred_persona_role
    for r in candidate.secondary_persona_roles:
        if r.startswith("competitor_user_"):
            return r
    return "none"


def evaluate_persona_diversity(
    *,
    brief: ProductBriefForPlanning,
    candidates: list[PersonaCandidate],
    plan: EvidenceAnchorPlan | None = None,
) -> PersonaDiversityEvaluation:
    """Score the diversity of a generated persona-candidate set.

    Universal rules:
      * No candidates → DEFER_NO_CANDIDATES.
      * All candidates share one primary role → narrow_source_proof_only,
        DEFER_SOURCE_COVERAGE.
      * One competitor brand dominates ≥85% of candidates →
        DEFER_DIVERSIFY (and warning).
      * 2+ unique primary roles + diversity_score >= 0.5 → READY.
      * Otherwise DEFER_DIVERSIFY.
    """
    n = len(candidates)
    if n == 0:
        return PersonaDiversityEvaluation(
            diversity_score=0.0,
            primary_role_count=0,
            unique_primary_roles=[],
            unique_secondary_roles=[],
            evidence_source_count=0,
            competitor_concentration=0.0,
            duplicate_role_cluster_count=0,
            persona_similarity_warnings=[
                "no persona candidates were generated"
            ],
            undercovered_evidence_themes=[
                "all evidence themes are uncovered (zero candidates)"
            ],
            mutating_persistence_recommendation="DEFER_NO_CANDIDATES",
            narrow_source_proof_only=False,
            rationale=[
                "Candidate count = 0. Either source pool is empty OR "
                "every candidate failed quality / launch-state gates."
            ],
        )

    primary_roles = [c.inferred_persona_role for c in candidates]
    primary_role_counter: Counter = Counter(primary_roles)
    unique_primary = sorted(set(primary_roles))

    secondary_pool: list[str] = []
    for c in candidates:
        secondary_pool.extend(c.secondary_persona_roles)
    unique_secondary = sorted(set(secondary_pool))

    source_ids: set[str] = set()
    for c in candidates:
        source_ids.update(c.source_record_ids)
    evidence_source_count = len(source_ids)

    # Competitor concentration: largest competitor cluster / N
    comp_keys = [_competitor_key(c) for c in candidates]
    comp_counter: Counter = Counter(comp_keys)
    # Exclude "none" from concentration math — it's the "no-competitor-role"
    # bucket, not a competitor.
    real_comp_counter = Counter(
        {k: v for k, v in comp_counter.items() if k != "none"}
    )
    if real_comp_counter:
        top_comp_count = real_comp_counter.most_common(1)[0][1]
        competitor_concentration = round(top_comp_count / n, 3)
    else:
        competitor_concentration = 0.0

    # Duplicate role clusters
    duplicate_role_cluster_count = sum(
        1 for v in primary_role_counter.values() if v >= 2
    )

    # Persona-similarity warnings
    warnings: list[str] = []
    if duplicate_role_cluster_count > 0:
        for role, count in primary_role_counter.most_common():
            if count >= 2:
                warnings.append(
                    f"{count} candidates share primary role "
                    f"{role!r} — duplicate-role cluster"
                )
    if competitor_concentration >= _COMPETITOR_CONCENTRATION_WARNING:
        top_comp = real_comp_counter.most_common(1)[0][0]
        warnings.append(
            f"{int(competitor_concentration * 100)}% of candidates "
            f"reference the same competitor "
            f"({top_comp.replace('competitor_user_', '')}); "
            "diversity is competitor-skewed"
        )

    # Undercovered evidence themes — based on brief.competitors that
    # are NOT represented in the candidates' competitor roles.
    represented_competitors: set[str] = set()
    for c in candidates:
        for r in [c.inferred_persona_role] + list(c.secondary_persona_roles):
            if r.startswith("competitor_user_"):
                represented_competitors.add(
                    r.replace("competitor_user_", "")
                )
    undercovered_themes: list[str] = []
    for c in brief.competitors:
        slug = c.lower().replace(" ", "_").replace("-", "_")
        if slug not in represented_competitors:
            undercovered_themes.append(
                f"no candidate references brief competitor "
                f"{c!r}; consider broader source coverage"
            )

    # Diversity score: blend of unique-roles-rate + non-competitor-skew
    role_uniqueness = len(unique_primary) / n
    competitor_balance = 1.0 - competitor_concentration
    diversity_score = round(
        0.6 * role_uniqueness + 0.4 * competitor_balance, 3,
    )

    # Recommendation
    rationale: list[str] = []
    rec: DiversityRecommendation
    narrow_source_proof_only = False
    if len(unique_primary) <= 1:
        rec = "DEFER_SOURCE_COVERAGE"
        narrow_source_proof_only = True
        rationale.append(
            "All candidates share a single primary role "
            f"({unique_primary[0] if unique_primary else 'unknown'!r}). "
            "Persona persistence would yield a same-voice cluster, "
            "not a useful mini-society. Source coverage must be "
            "broadened before persistence."
        )
    elif competitor_concentration >= _COMPETITOR_CONCENTRATION_HARD:
        rec = "DEFER_DIVERSIFY"
        rationale.append(
            f"{int(competitor_concentration * 100)}% of candidates "
            "concentrate on a single competitor. Diversify the "
            "evidence selection or broaden source coverage."
        )
    elif (
        len(unique_primary) >= _MIN_UNIQUE_ROLES_READY
        and diversity_score >= _DIVERSITY_SCORE_READY_THRESHOLD
    ):
        rec = "READY"
        rationale.append(
            f"{len(unique_primary)} unique primary roles + "
            f"diversity_score {diversity_score} >= "
            f"{_DIVERSITY_SCORE_READY_THRESHOLD} threshold."
        )
    else:
        rec = "DEFER_DIVERSIFY"
        rationale.append(
            f"diversity_score {diversity_score} < "
            f"{_DIVERSITY_SCORE_READY_THRESHOLD}; defer until evidence "
            "selection produces a more even role distribution."
        )

    return PersonaDiversityEvaluation(
        diversity_score=diversity_score,
        primary_role_count=n,
        unique_primary_roles=unique_primary,
        unique_secondary_roles=unique_secondary,
        evidence_source_count=evidence_source_count,
        competitor_concentration=competitor_concentration,
        duplicate_role_cluster_count=duplicate_role_cluster_count,
        persona_similarity_warnings=warnings,
        undercovered_evidence_themes=undercovered_themes,
        mutating_persistence_recommendation=rec,
        narrow_source_proof_only=narrow_source_proof_only,
        rationale=rationale,
    )
