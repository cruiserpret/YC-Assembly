"""Phase 8.2H — operator-text formatter + next-step recommender."""
from __future__ import annotations

from collections.abc import Sequence

from assembly.pipeline.audience_retrieval.schemas import (
    CategoryCoverageLabel,
    NextStepRecommendation,
    PersonaMatch,
    ReadinessByMode,
    RunScopedAudienceRetrievalResult,
    TopUpRecommendation,
)


def explain_next_step_recommendation(
    *,
    readiness: ReadinessByMode,
    matched: Sequence[PersonaMatch],
    topup: Sequence[TopUpRecommendation],
    has_sensitive_caveats: bool,
) -> NextStepRecommendation:
    """Decision logic:

      * E (HOLD_FOR_COMPLIANCE_REVIEW) if there are sensitive caveats AND
        any top-up requires extra compliance review.
      * D (RUN_TOPUP_INGESTION_FIRST) if not tiny_ready or top-up list
        is non-empty.
      * C / B / A based on highest readiness mode true.
    """
    if has_sensitive_caveats and any(
        t.requires_extra_compliance_review for t in topup
    ):
        return NextStepRecommendation.HOLD_FOR_COMPLIANCE_REVIEW
    if not readiness.tiny_ready or topup:
        return NextStepRecommendation.RUN_TOPUP_INGESTION_FIRST
    if readiness.serious_ready:
        return NextStepRecommendation.PROCEED_TO_SERIOUS_SIMULATION
    if readiness.small_ready:
        return NextStepRecommendation.PROCEED_TO_SMALL_SIMULATION
    return NextStepRecommendation.PROCEED_TO_TINY_SIMULATION


def render_audience_retrieval_summary(
    result: RunScopedAudienceRetrievalResult,
) -> str:
    """Compact human-readable summary."""
    lines: list[str] = []
    bar = "=" * 64
    lines.append(bar)
    lines.append("Run-scoped Audience Retrieval Result")
    lines.append(bar)
    lines.append(f"brief: {result.brief_summary[:120]}")
    lines.append(f"plan:  {result.target_society_plan_summary[:200]}")
    lines.append("")
    lines.append(
        f"matched personas:   {len(result.matched_personas)}"
    )
    lines.append(
        f"excluded personas:  {len(result.excluded_personas)}"
    )
    lines.append("")
    lines.append("Per-category coverage:")
    for c in result.category_coverage:
        marker = {
            CategoryCoverageLabel.MISSING: "✗",
            CategoryCoverageLabel.THIN: "·",
            CategoryCoverageLabel.ACCEPTABLE_FOR_TINY: "▴",
            CategoryCoverageLabel.ACCEPTABLE_FOR_SMALL: "▴▴",
            CategoryCoverageLabel.ACCEPTABLE_FOR_SERIOUS: "✓",
        }[c.coverage_label]
        lines.append(
            f"  {marker} [{c.priority}] {c.category_key}: "
            f"matched={c.matched_total} "
            f"(high={c.matched_highly_relevant}, "
            f"rel={c.matched_relevant}, "
            f"weak={c.matched_weak}) "
            f"label={c.coverage_label.value} "
            f"(targets t={c.required_min_tiny} s={c.required_min_small} "
            f"S={c.required_min_serious})"
        )
    lines.append("")

    sd = result.source_diversity_summary
    lines.append(
        f"Source diversity: {sd.distinct_source_domains} domains "
        f"(min required {sd.minimum_required}); "
        f"single_source_risk={sd.single_source_risk}"
    )
    if sd.domains:
        lines.append("  domains: " + ", ".join(sd.domains[:8]))
    lines.append("")

    r = result.readiness_by_mode
    lines.append(
        f"Readiness — tiny={r.tiny_ready}  small={r.small_ready}  "
        f"serious={r.serious_ready}"
    )
    for b in r.blocked_reasons:
        lines.append(f"  ✗ blocked: {b}")
    for c in r.caveats:
        lines.append(f"  · caveat: {c}")
    lines.append("")

    if result.matched_personas:
        lines.append("Top matched personas (first 5 by score):")
        sorted_m = sorted(
            result.matched_personas,
            key=lambda m: m.relevance_score, reverse=True,
        )
        for m in sorted_m[:5]:
            lines.append(
                f"  - {m.display_name}  score={m.relevance_score}/45  "
                f"[{m.classification.value}]  → {m.matched_category_key}"
            )
    lines.append("")

    if result.topup_recommendations:
        lines.append(f"Top-up recommendations ({len(result.topup_recommendations)}):")
        for t in result.topup_recommendations[:8]:
            tag = " ⚠compliance" if t.requires_extra_compliance_review else ""
            lines.append(
                f"  - {t.stakeholder_category_key}{tag}: "
                f"max={t.max_records_suggested}, "
                f"yield={t.expected_persona_yield_range}, "
                f"queries={len(t.suggested_queries)}"
            )
            if t.suggested_queries:
                lines.append(f"      first query: {t.suggested_queries[0]}")
    else:
        lines.append("Top-up recommendations: <none — coverage acceptable>")
    lines.append("")

    if result.warnings_and_caveats:
        lines.append("Warnings / caveats:")
        for w in result.warnings_and_caveats:
            lines.append(f"  · {w}")
        lines.append("")

    lines.append(f"NEXT STEP: {result.next_step_recommendation.value}")
    lines.append(bar)
    return "\n".join(lines)


def render_operator_report(
    result: RunScopedAudienceRetrievalResult,
) -> str:
    """One-paragraph summary."""
    n = len(result.matched_personas)
    n_x = len(result.excluded_personas)
    n_topup = len(result.topup_recommendations)
    return (
        f"matched={n}  excluded={n_x}  topup_recs={n_topup}  "
        f"tiny_ready={result.readiness_by_mode.tiny_ready}  "
        f"next={result.next_step_recommendation.value}"
    )
