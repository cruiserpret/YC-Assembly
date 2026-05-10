"""Phase 8.2G — operator-facing summary formatters."""
from __future__ import annotations

from assembly.pipeline.target_society.schemas import (
    SocietyPlanWarning,
    StakeholderCategory,
    TargetSocietyPlan,
)


def render_target_society_plan_summary(plan: TargetSocietyPlan) -> str:
    """Compact human-readable summary."""
    lines: list[str] = []
    bar = "=" * 64
    lines.append(bar)
    lines.append(
        f"Target Society Plan — family="
        f"{plan.interpreted_brief.detected_product_family.value}"
    )
    lines.append(bar)
    lines.append(f"product_summary: {plan.interpreted_brief.product_summary}")
    if plan.interpreted_brief.target_market_interpretation:
        lines.append(
            f"target_market: {plan.interpreted_brief.target_market_interpretation}"
        )
    if plan.interpreted_brief.competitor_interpretation:
        lines.append(
            f"competitors:   {plan.interpreted_brief.competitor_interpretation}"
        )
    if plan.interpreted_brief.price_context:
        lines.append(f"price:         {plan.interpreted_brief.price_context}")
    if plan.interpreted_brief.geography_context:
        lines.append(f"geography:     {plan.interpreted_brief.geography_context}")
    if plan.interpreted_brief.missing_inputs:
        lines.append(
            f"missing_inputs: {', '.join(plan.interpreted_brief.missing_inputs)}"
        )
    lines.append("")

    lines.append(
        f"Stakeholder categories ({len(plan.stakeholder_categories)}):"
    )
    for c in plan.stakeholder_categories:
        sens = " [SENSITIVE]" if c.sensitivity_or_compliance_notes else ""
        lines.append(
            f"  - [{c.priority}] {c.category_key}: {c.display_name}{sens}"
        )
        lines.append(f"      why: {c.why_relevant}")
        if c.likely_pains:
            lines.append(f"      pains: {', '.join(c.likely_pains[:3])}")
        if c.likely_objections:
            lines.append(f"      objections: {', '.join(c.likely_objections[:3])}")
        if c.likely_current_alternatives:
            lines.append(
                f"      alternatives: {', '.join(c.likely_current_alternatives[:3])}"
            )
        lines.append(
            f"      targets: tiny={c.minimum_persona_target_tiny} "
            f"small={c.minimum_persona_target_small} "
            f"serious={c.minimum_persona_target_serious}"
        )
    lines.append("")

    lines.append("Source query plan (first query per category):")
    for q in plan.source_query_plan:
        head = q.queries[0] if q.queries else "<no queries>"
        lines.append(f"  - {q.category_key}: {head}")
        if q.competitor_queries:
            lines.append(
                f"      competitor queries: {q.competitor_queries[0]}"
            )
        if q.geography_queries:
            lines.append(
                f"      geography queries:  {q.geography_queries[0]}"
            )
    lines.append("")

    lines.append("Coverage requirements:")
    cov = plan.coverage_requirements
    lines.append(
        f"  min categories: {cov.minimum_categories_represented} | "
        f"min strong shells: {cov.minimum_strong_persona_signal_shells} | "
        f"min source domains: {cov.minimum_source_diversity_domains}"
    )
    lines.append(
        f"  geography_required: {cov.geography_coverage_required} | "
        f"competitor_required: {cov.competitor_evidence_required} | "
        f"price_required: {cov.price_evidence_required}"
    )
    lines.append("")

    lines.append("Simulation readiness gates:")
    g = plan.simulation_readiness_gates
    lines.append(
        f"  tiny={g.tiny_minimum_personas}  small={g.small_minimum_personas}  "
        f"serious={g.serious_minimum_personas}  scaled={g.scaled_minimum_personas}"
    )
    lines.append(
        f"  block_if_single_source={g.block_if_single_source} | "
        f"block_if_key_category_missing={g.block_if_key_category_missing} | "
        f"block_if_thin_geography={g.block_if_thin_geography} | "
        f"block_if_no_competitor_evidence={g.block_if_no_competitor_evidence}"
    )
    lines.append("")

    lines.append("Expected outputs:")
    for q in plan.expected_outputs.answerable_questions[:5]:
        lines.append(f"  ✓ {q}")
    for q in plan.expected_outputs.unanswerable_questions[:5]:
        lines.append(f"  ✗ {q}")
    lines.append("")

    lines.append("Warnings / limitations:")
    for w in plan.warnings_and_limitations:
        lines.append(f"  [{w.severity.value}] {w.code}: {w.message}")
    lines.append(bar)
    return "\n".join(lines)


def render_operator_summary(plan: TargetSocietyPlan) -> str:
    """One-paragraph operator-readable summary."""
    cats = ", ".join(c.category_key for c in plan.stakeholder_categories[:6])
    fam = plan.interpreted_brief.detected_product_family.value
    return (
        f"Family={fam}; "
        f"{len(plan.stakeholder_categories)} stakeholder categories "
        f"({cats}…); "
        f"tiny={plan.simulation_readiness_gates.tiny_minimum_personas} "
        f"serious={plan.simulation_readiness_gates.serious_minimum_personas} "
        f"warnings={len(plan.warnings_and_limitations)}."
    )


def explain_next_steps(plan: TargetSocietyPlan) -> list[str]:
    """Return the operator-facing checklist of what to do next.

    The list deliberately stops at the boundary of Phase 8.2G — it
    points to Phase 8.2H (audience retrieval / top-up planning) but
    does NOT execute it.
    """
    steps: list[str] = []
    steps.append(
        "Review the stakeholder categories — confirm each one is on-target "
        "for the brief; flag any extras to drop OR missing categories to "
        "add."
    )
    if plan.interpreted_brief.missing_inputs:
        steps.append(
            "Provide the missing brief inputs ("
            + ", ".join(plan.interpreted_brief.missing_inputs)
            + ") if available; the planner can re-run with them."
        )
    sensitive = any(
        c.sensitivity_or_compliance_notes
        for c in plan.stakeholder_categories
    )
    if sensitive:
        steps.append(
            "Sensitive / protected-attribute markers detected — confirm "
            "compliance posture before any downstream ingestion or "
            "persona retrieval."
        )
    steps.append(
        "Phase 8.2H will use this plan to retrieve existing personas + "
        "trigger top-up Tavily ingestion ONLY for categories below the "
        "tiny / small / serious thresholds. Phase 8.2G does not execute "
        "any retrieval."
    )
    steps.append(
        "Phase 8.2I+ will use the readiness gates to decide whether the "
        "society is simulation-ready. This plan is ready to drive that "
        "audit once retrieval populates the pool."
    )
    return steps
