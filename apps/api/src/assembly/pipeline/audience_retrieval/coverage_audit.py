"""Phase 8.2H — coverage + readiness audit.

Pure functions. Take the matched-persona list + the TargetSocietyPlan
and compute:

  - `compute_category_coverage`     per-category match counts +
                                    coverage_label
  - `compute_source_diversity`      distinct-domain count + flag
  - `compute_readiness_by_mode`     tiny / small / serious gates
  - `detect_missing_key_categories` high-priority categories with
                                    zero matches
  - `detect_single_source_risk`     boolean + reason
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from assembly.pipeline.persona_relevance.rubric import RelevanceClassification
from assembly.pipeline.audience_retrieval.schemas import (
    CategoryCoverage,
    CategoryCoverageLabel,
    PersonaMatch,
    ReadinessByMode,
    SourceDiversitySummary,
)
from assembly.pipeline.target_society.schemas import (
    StakeholderCategory,
    TargetSocietyPlan,
)


def compute_category_coverage(
    *,
    plan: TargetSocietyPlan,
    matched: Sequence[PersonaMatch],
) -> list[CategoryCoverage]:
    """Build one CategoryCoverage row per stakeholder category.

    Counts include only personas classified `relevant` or
    `highly_relevant`. Weakly-relevant personas have already been
    excluded by the matcher, but we still surface
    `matched_weak` as zero so the output schema is stable.
    """
    by_cat: dict[str, list[PersonaMatch]] = {}
    for m in matched:
        by_cat.setdefault(m.matched_category_key, []).append(m)

    out: list[CategoryCoverage] = []
    for cat in plan.stakeholder_categories:
        bucket = by_cat.get(cat.category_key, [])
        n_high = sum(
            1 for m in bucket
            if m.classification == RelevanceClassification.HIGHLY_RELEVANT
        )
        n_rel = sum(
            1 for m in bucket
            if m.classification == RelevanceClassification.RELEVANT
        )
        n_weak = sum(
            1 for m in bucket
            if m.classification == RelevanceClassification.WEAKLY_RELEVANT
        )
        n_total = n_high + n_rel + n_weak
        label = _coverage_label(
            total=n_total,
            tiny_target=cat.minimum_persona_target_tiny,
            small_target=cat.minimum_persona_target_small,
            serious_target=cat.minimum_persona_target_serious,
        )
        topup_queries = (
            _topup_queries_for(plan, cat) if label in (
                CategoryCoverageLabel.MISSING,
                CategoryCoverageLabel.THIN,
                CategoryCoverageLabel.ACCEPTABLE_FOR_TINY,
            ) else []
        )
        missing_signals: list[str] = []
        if label is CategoryCoverageLabel.MISSING:
            missing_signals.append(
                f"no existing persona matches this category"
            )
        elif label is CategoryCoverageLabel.THIN:
            missing_signals.append(
                f"only {n_total} match(es); below tiny target "
                f"({cat.minimum_persona_target_tiny})"
            )

        out.append(CategoryCoverage(
            category_key=cat.category_key,
            display_name=cat.display_name,
            priority=cat.priority,
            required_min_tiny=cat.minimum_persona_target_tiny,
            required_min_small=cat.minimum_persona_target_small,
            required_min_serious=cat.minimum_persona_target_serious,
            matched_highly_relevant=n_high,
            matched_relevant=n_rel,
            matched_weak=n_weak,
            matched_total=n_total,
            coverage_label=label,
            missing_signals=missing_signals,
            recommended_topup_queries=topup_queries,
        ))
    return out


def _coverage_label(
    *, total: int, tiny_target: int, small_target: int, serious_target: int,
) -> CategoryCoverageLabel:
    if total <= 0:
        return CategoryCoverageLabel.MISSING
    if total < tiny_target:
        return CategoryCoverageLabel.THIN
    if total >= serious_target:
        return CategoryCoverageLabel.ACCEPTABLE_FOR_SERIOUS
    if total >= small_target:
        return CategoryCoverageLabel.ACCEPTABLE_FOR_SMALL
    return CategoryCoverageLabel.ACCEPTABLE_FOR_TINY


def _topup_queries_for(
    plan: TargetSocietyPlan, cat: StakeholderCategory,
) -> list[str]:
    """Pull the per-category source-query plan from the
    TargetSocietyPlan (Phase 8.2G generated it) and surface a copy as
    "recommended top-up queries" for this category."""
    for q in plan.source_query_plan:
        if q.category_key == cat.category_key:
            return list(q.queries)
    return []


def compute_source_diversity(
    *,
    matched: Sequence[PersonaMatch],
    minimum_required: int,
) -> SourceDiversitySummary:
    domains: set[str] = set()
    for m in matched:
        for d in m.source_domains:
            if d:
                domains.add(d)
    return SourceDiversitySummary(
        distinct_source_domains=len(domains),
        domains=sorted(domains),
        minimum_required=minimum_required,
        single_source_risk=(len(domains) <= 1 and len(matched) >= 2),
    )


def detect_missing_key_categories(
    *, coverage: Iterable[CategoryCoverage],
) -> list[str]:
    """High-priority categories with `MISSING` or `THIN` coverage."""
    out: list[str] = []
    for c in coverage:
        if c.priority == "high" and c.coverage_label in (
            CategoryCoverageLabel.MISSING, CategoryCoverageLabel.THIN,
        ):
            out.append(c.category_key)
    return out


def compute_readiness_by_mode(
    *,
    plan: TargetSocietyPlan,
    coverage: Sequence[CategoryCoverage],
    diversity: SourceDiversitySummary,
    matched_total: int,
) -> ReadinessByMode:
    """Compute tiny / small / serious readiness based on:

      - matched_total >= the plan's mode-specific minimum_personas
      - distinct stakeholder categories with non-zero coverage >=
        coverage_requirements.minimum_categories_represented
      - block flags on the plan
    """
    n_categories_with_any = sum(
        1 for c in coverage if c.matched_total > 0
    )
    n_categories_at_tiny = sum(
        1 for c in coverage
        if c.coverage_label not in (
            CategoryCoverageLabel.MISSING, CategoryCoverageLabel.THIN,
        )
    )
    n_categories_at_small = sum(
        1 for c in coverage
        if c.coverage_label in (
            CategoryCoverageLabel.ACCEPTABLE_FOR_SMALL,
            CategoryCoverageLabel.ACCEPTABLE_FOR_SERIOUS,
        )
    )
    n_categories_at_serious = sum(
        1 for c in coverage
        if c.coverage_label == CategoryCoverageLabel.ACCEPTABLE_FOR_SERIOUS
    )
    min_categories = plan.coverage_requirements.minimum_categories_represented

    blocked: list[str] = []
    caveats: list[str] = []

    gates = plan.simulation_readiness_gates
    if gates.block_if_single_source and diversity.single_source_risk:
        blocked.append(
            "Single-source risk: only one source domain across all matched "
            "personas. Block until source diversity reaches "
            f"{diversity.minimum_required} distinct domains."
        )
    missing_high = detect_missing_key_categories(coverage=coverage)
    if gates.block_if_key_category_missing and missing_high:
        blocked.append(
            "High-priority stakeholder category(ies) missing or thin: "
            + ", ".join(missing_high)
        )
    if gates.block_if_thin_geography and plan.coverage_requirements.geography_coverage_required:
        # Geography categories are flagged as "missing" or "thin" iff
        # no geography-shaped matched persona exists.
        geo_thin = any(
            c.category_key.startswith("geography_")
            and c.coverage_label in (
                CategoryCoverageLabel.MISSING,
                CategoryCoverageLabel.THIN,
            )
            for c in coverage
        )
        if geo_thin:
            blocked.append(
                "Geography coverage is thin and the plan requires "
                "geography evidence."
            )
    if gates.block_if_no_competitor_evidence:
        # Competitor evidence = any current_alternative_* category has
        # at least one match OR any matched persona's
        # current_alternative trait mentions a competitor.
        any_competitor_match = any(
            c.category_key.startswith("current_alternative_")
            and c.matched_total > 0
            for c in coverage
        )
        if not any_competitor_match and any(
            c.category_key.startswith("current_alternative_")
            for c in coverage
        ):
            blocked.append(
                "No personas matched any current_alternative_* "
                "stakeholder category."
            )

    tiny_threshold = gates.tiny_minimum_personas
    small_threshold = gates.small_minimum_personas
    serious_threshold = gates.serious_minimum_personas

    tiny_ready = (
        not blocked
        and matched_total >= 1
        and n_categories_with_any >= max(2, min_categories - 2)
    )
    small_ready = (
        tiny_ready
        and matched_total >= small_threshold
        and n_categories_at_small >= min_categories - 1
    )
    serious_ready = (
        small_ready
        and matched_total >= serious_threshold
        and n_categories_at_serious >= min_categories
    )

    if tiny_ready and not small_ready:
        caveats.append(
            "Tiny mode allowed with caveat: matched-persona pool is "
            "thin; treat output as directional only."
        )
    if not tiny_ready:
        caveats.append(
            "Tiny mode blocked. Run top-up ingestion before any simulation."
        )

    return ReadinessByMode(
        tiny_ready=tiny_ready,
        small_ready=small_ready,
        serious_ready=serious_ready,
        blocked_reasons=blocked,
        caveats=caveats,
    )


def detect_single_source_risk(diversity: SourceDiversitySummary) -> bool:
    return diversity.single_source_risk
