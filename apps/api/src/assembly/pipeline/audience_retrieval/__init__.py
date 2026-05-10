"""Phase 8.2H — run-scoped audience retrieval + top-up planning.

Public surface:

    retrieve_personas_for_target_society()
        Top-level: brief + plan + existing-persona-pool
        → RunScopedAudienceRetrievalResult.

    score_persona_against_category()
        Per-(persona, category) deterministic score (8 axes + penalty).

    match_personas_to_categories()
        Pure matcher. Assigns each persona to its best category;
        partitions into matched / excluded.

    compute_category_coverage()
    compute_source_diversity()
    compute_readiness_by_mode()
        Audit functions.

    build_topup_recommendations()
        Per-category top-up plan.

    render_audience_retrieval_summary()
    render_operator_report()
        Operator-text formatters.
"""
from collections.abc import Mapping, Sequence
from uuid import UUID

from assembly.pipeline.persona_relevance.auditor import PersonaAuditInput
from assembly.pipeline.target_society.schemas import (
    ProductBriefInput,
    TargetSocietyPlan,
)

from assembly.pipeline.audience_retrieval.coverage_audit import (
    compute_category_coverage,
    compute_readiness_by_mode,
    compute_source_diversity,
    detect_missing_key_categories,
    detect_single_source_risk,
)
from assembly.pipeline.audience_retrieval.retriever import (
    match_personas_to_categories,
)
from assembly.pipeline.audience_retrieval.schemas import (
    CategoryCoverage,
    CategoryCoverageLabel,
    NextStepRecommendation,
    PersonaExclusion,
    PersonaMatch,
    ReadinessByMode,
    RunScopedAudienceRetrievalResult,
    SourceDiversitySummary,
    TopUpRecommendation,
)
from assembly.pipeline.audience_retrieval.scorer import (
    CategoryScoreBreakdown,
    classify_persona_match,
    score_persona_against_category,
)
from assembly.pipeline.audience_retrieval.weights import (
    TOTAL_WEIGHT_SUM,
    UNIFORM_WEIGHTS,
    WEIGHTED_AXES,
    apply_weights_to_breakdown,
    derive_scorer_weights_for_plan,
)
from assembly.pipeline.audience_retrieval.summary import (
    explain_next_step_recommendation,
    render_audience_retrieval_summary,
    render_operator_report,
)
from assembly.pipeline.audience_retrieval.topup import (
    build_topup_recommendations,
    convert_target_society_queries_to_topup_plan,
)


def _plan_has_sensitive_caveats(plan: TargetSocietyPlan) -> bool:
    from assembly.pipeline.target_society.constants import (
        WARNING_PROTECTED_ATTRIBUTE_INFERENCE_FORBIDDEN,
        WARNING_SENSITIVE_TARGETING_CAVEAT,
    )
    for w in plan.warnings_and_limitations:
        if w.code in (
            WARNING_SENSITIVE_TARGETING_CAVEAT,
            WARNING_PROTECTED_ATTRIBUTE_INFERENCE_FORBIDDEN,
        ):
            return True
    return False


def retrieve_personas_for_target_society(
    *,
    brief: ProductBriefInput,
    plan: TargetSocietyPlan,
    personas: Sequence[PersonaAuditInput],
    domain_by_record_id: Mapping[UUID, str] | None = None,
) -> RunScopedAudienceRetrievalResult:
    """End-to-end run-scoped audience retrieval.

    Pure function. No DB, no LLM, no network. The caller pre-loads
    persona / trait / evidence-link rows into `PersonaAuditInput` and
    optionally pre-builds a `domain_by_record_id` map so PersonaMatch
    rows carry source-host context.
    """
    matched, excluded = match_personas_to_categories(
        plan=plan,
        personas=personas,
        domain_by_record_id=domain_by_record_id,
    )
    coverage = compute_category_coverage(plan=plan, matched=matched)
    diversity = compute_source_diversity(
        matched=matched,
        minimum_required=(
            plan.coverage_requirements.minimum_source_diversity_domains
        ),
    )
    readiness = compute_readiness_by_mode(
        plan=plan,
        coverage=coverage,
        diversity=diversity,
        matched_total=len(matched),
    )
    topup = build_topup_recommendations(plan=plan, coverage=coverage)

    has_sensitive = _plan_has_sensitive_caveats(plan)
    next_step = explain_next_step_recommendation(
        readiness=readiness,
        matched=matched,
        topup=topup,
        has_sensitive_caveats=has_sensitive,
    )

    warnings_and_caveats: list[str] = []
    if has_sensitive:
        warnings_and_caveats.append(
            "Brief carries sensitive / protected-attribute caveats. "
            "Persona matching is broad-context only; no individual-"
            "level protected-attribute inference performed."
        )
    if diversity.single_source_risk:
        warnings_and_caveats.append(
            "Single-source risk: matched personas come from one source "
            "domain. Source diversity must be expanded before serious "
            "simulation."
        )
    missing_high = detect_missing_key_categories(coverage=coverage)
    if missing_high:
        warnings_and_caveats.append(
            "High-priority stakeholder categories with thin/missing "
            "coverage: " + ", ".join(missing_high)
        )

    plan_summary = (
        f"family={plan.interpreted_brief.detected_product_family.value}; "
        f"{len(plan.stakeholder_categories)} stakeholder categories; "
        f"{len(plan.warnings_and_limitations)} warnings."
    )
    brief_summary = (
        f"{brief.product_name}: {brief.product_description[:200]}"
    )

    return RunScopedAudienceRetrievalResult(
        brief_summary=brief_summary,
        target_society_plan_summary=plan_summary,
        matched_personas=matched,
        excluded_personas=excluded,
        category_coverage=coverage,
        source_diversity_summary=diversity,
        readiness_by_mode=readiness,
        topup_recommendations=topup,
        warnings_and_caveats=warnings_and_caveats,
        next_step_recommendation=next_step,
    )


__all__ = [
    "CategoryCoverage",
    "CategoryCoverageLabel",
    "CategoryScoreBreakdown",
    "NextStepRecommendation",
    "PersonaExclusion",
    "PersonaMatch",
    "ReadinessByMode",
    "RunScopedAudienceRetrievalResult",
    "SourceDiversitySummary",
    "TopUpRecommendation",
    "build_topup_recommendations",
    "classify_persona_match",
    "compute_category_coverage",
    "compute_readiness_by_mode",
    "compute_source_diversity",
    "convert_target_society_queries_to_topup_plan",
    "detect_missing_key_categories",
    "detect_single_source_risk",
    "explain_next_step_recommendation",
    "match_personas_to_categories",
    "render_audience_retrieval_summary",
    "render_operator_report",
    "retrieve_personas_for_target_society",
    "score_persona_against_category",
]
