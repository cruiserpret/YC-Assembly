"""Phase 8.2G — coverage requirements + simulation readiness gates.

Pure functions over the brief + family + categories. No DB access.
"""
from __future__ import annotations

from assembly.pipeline.target_society.constants import ProductFamily
from assembly.pipeline.target_society.schemas import (
    CoverageRequirements,
    ProductBriefInput,
    SimulationReadinessGates,
    StakeholderCategory,
)


# Tunable defaults — explicit so an operator can read them.
_DEFAULT_TINY = 6           # 6 personas across categories minimum
_DEFAULT_SMALL = 20
_DEFAULT_SERIOUS = 80
_DEFAULT_SCALED = 250

_DEFAULT_MIN_DOMAINS = 5    # source diversity (distinct hosts)
_DEFAULT_MIN_DIRECT_INFERRED_PER_PERSONA = 3


def build_coverage_requirements(
    *,
    brief: ProductBriefInput,
    family: ProductFamily,
    categories: list[StakeholderCategory],
    is_market_entry: bool = False,
) -> CoverageRequirements:
    """Compute coverage requirements from the brief + categories.

    Rules:
      - minimum_categories_represented: at least 4 OR
        the count of high-priority categories — whichever is greater.
      - minimum_strong_persona_signal_shells: serious-mode tiny target
        across all high-priority categories.
      - minimum_source_diversity_domains: 5 by default.
      - minimum_direct_inferred_traits_per_persona: 3 (matches the
        Phase 8.2F worker's persistence threshold).
      - geography_coverage_required: True iff brief.geography is set.
      - competitor_evidence_required: True iff brief.competitors is set.
      - price_evidence_required: True iff brief.price_or_price_structure
        is set OR family is consumer-packaged-good / consumer-electronics
        (price is structurally important for those).
      - trust_objection_evidence_required: always True (every product
        sim needs trust + objection signal).
    """
    n_high = sum(1 for c in categories if c.priority == "high")
    return CoverageRequirements(
        minimum_categories_represented=max(4, n_high),
        minimum_strong_persona_signal_shells=sum(
            c.minimum_persona_target_serious
            for c in categories
            if c.priority == "high"
        ) or 12,
        minimum_source_diversity_domains=_DEFAULT_MIN_DOMAINS,
        minimum_direct_inferred_traits_per_persona=(
            _DEFAULT_MIN_DIRECT_INFERRED_PER_PERSONA
        ),
        # Phase 8.4A.2: in market-entry mode, geography is a SOFT
        # bonus, not a hard gate — non-California category evidence
        # is still relevant when the local-evidence pool is thin.
        geography_coverage_required=(
            False if is_market_entry else bool(brief.geography)
        ),
        competitor_evidence_required=bool(brief.competitors),
        price_evidence_required=(
            bool(brief.price_or_price_structure)
            or family in (
                ProductFamily.CONSUMER_PACKAGED_GOOD,
                ProductFamily.CONSUMER_ELECTRONICS,
            )
        ),
        trust_objection_evidence_required=True,
    )


def build_readiness_gates(
    *,
    brief: ProductBriefInput,
    family: ProductFamily,
    categories: list[StakeholderCategory],
) -> SimulationReadinessGates:
    """Compute readiness gates.

    Tiny / small / serious / scaled minimums scale with the category
    count + priority distribution. Block-flags are True when the brief
    explicitly carries that input (e.g. block_if_no_competitor_evidence
    is True only when the brief named competitors)."""
    n_high = sum(1 for c in categories if c.priority == "high")
    tiny = max(_DEFAULT_TINY, n_high * 2)
    small = max(_DEFAULT_SMALL, n_high * 4)
    serious = max(_DEFAULT_SERIOUS, n_high * 12)
    scaled = max(_DEFAULT_SCALED, n_high * 30)

    return SimulationReadinessGates(
        tiny_minimum_personas=tiny,
        small_minimum_personas=small,
        serious_minimum_personas=serious,
        scaled_minimum_personas=scaled,
        block_if_single_source=True,
        block_if_key_category_missing=True,
        block_if_thin_geography=bool(brief.geography),
        block_if_no_competitor_evidence=bool(brief.competitors),
        allow_tiny_mode_with_caveat=True,
    )
