"""Phase 8.2H — scorer tests (pure)."""
from __future__ import annotations

from uuid import UUID, uuid4

from assembly.pipeline.audience_retrieval.scorer import (
    classify_persona_match,
    score_persona_against_category,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    WATER_BOTTLE_BRIEF,
    build_target_society_plan,
)


def _trait(field_name, support_level, value, source_ids=()):
    return TraitView(
        field_name=field_name,
        support_level=support_level,
        value=value,
        confidence=0.85,
        source_ids=tuple(source_ids),
    )


def _link(persona_id, source_id, *, excerpt, field, kind="direct", likely=True):
    return EvidenceLinkView(
        persona_id=persona_id,
        source_record_id=source_id,
        contribution_kind=kind,
        contribution_field=field,
        excerpt=excerpt,
        source_likely_human_signal=likely,
    )


def _amboras_persona() -> PersonaAuditInput:
    pid = uuid4()
    s1, s2, s3, s4 = uuid4(), uuid4(), uuid4(), uuid4()
    return PersonaAuditInput(
        persona_id=pid,
        display_name="Tatum G.",
        traits=(
            _trait("role_or_context", "direct",
                   "Shopify merchant doing $30k/month", (s1,)),
            _trait("objection_patterns", "direct",
                   "frustrated with plugin bloat and too many apps", (s2,)),
            _trait("current_alternatives", "direct",
                   "Shopify apps Klaviyo Oberlo agency", (s3,)),
            _trait("price_sensitivity", "direct",
                   "high; cumulative monthly fee plus plugin fees", (s4,)),
            _trait("trust_triggers", "inferred",
                   "wants brand control and transparency", (s2,)),
            _trait("interests", "direct",
                   "ecommerce store management", (s1,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="I'm a Shopify merchant doing $30k/month"),
            _link(pid, s2, field="objection_patterns",
                  excerpt="my plugin stack is overwhelming and I'm fed up"),
            _link(pid, s3, field="current_alternatives",
                  excerpt="using Klaviyo and Oberlo"),
            _link(pid, s4, field="price_sensitivity",
                  excerpt="cumulative monthly fees are expensive"),
        ),
    )


def _water_bottle_persona() -> PersonaAuditInput:
    pid = uuid4()
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    return PersonaAuditInput(
        persona_id=pid,
        display_name="Casey W.",
        traits=(
            _trait("role_or_context", "direct",
                   "regular grocery shopper buying premium bottled water", (s1,)),
            _trait("price_sensitivity", "direct",
                   "high; refuses to pay $10 for bottled water", (s2,)),
            _trait("objection_patterns", "direct",
                   "this is just water in a bottle; rip-off", (s3,)),
            _trait("current_alternatives", "direct",
                   "Aquafina and tap water at home", (s1,)),
            _trait("interests", "direct",
                   "price comparison and store private label", (s2,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="I'm a grocery shopper who buys bottled water"),
            _link(pid, s2, field="price_sensitivity",
                  excerpt="$10 for water is ridiculous; not worth $X"),
            _link(pid, s3, field="objection_patterns",
                  excerpt="this is just water in a bottle; rip-off"),
        ),
    )


# ---------------------------------------------------------------------------
# Cross-domain matching: Amboras-shape persona scores high on Amboras
# stakeholder, low on water-bottle stakeholder.
# ---------------------------------------------------------------------------


def test_amboras_persona_scores_high_on_amboras_category() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    p = _amboras_persona()
    cat = next(
        c for c in plan.stakeholder_categories
        if c.category_key == "shopify_or_platform_merchant"
    )
    br = score_persona_against_category(
        p, cat,
        geography_required=plan.coverage_requirements.geography_coverage_required,
    )
    assert br.total_score >= 27, (
        f"Amboras-shape persona should score >= 27 on the matching "
        f"category; got {br.total_score} ({br})"
    )
    assert classify_persona_match(br.total_score) in (
        RelevanceClassification.RELEVANT,
        RelevanceClassification.HIGHLY_RELEVANT,
    )


def test_amboras_persona_scores_low_on_water_bottle_category() -> None:
    """Cross-domain isolation: an Amboras-shape persona scoring against
    a water-bottle plan must NOT clear the relevant threshold for any
    water-bottle category."""
    water_plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    p = _amboras_persona()
    geography_required = water_plan.coverage_requirements.geography_coverage_required
    best_score = max(
        score_persona_against_category(p, c, geography_required=geography_required).total_score
        for c in water_plan.stakeholder_categories
    )
    assert best_score < 27, (
        f"Amboras persona should not score relevant on any water-bottle "
        f"category; best was {best_score}"
    )


def test_water_bottle_persona_scores_high_on_water_bottle_category() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    p = _water_bottle_persona()
    geography_required = plan.coverage_requirements.geography_coverage_required
    best_score = max(
        score_persona_against_category(p, c, geography_required=geography_required).total_score
        for c in plan.stakeholder_categories
    )
    assert best_score >= 18, (
        f"Water-bottle persona should at least reach weakly_relevant on "
        f"its own plan; got {best_score}"
    )


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


def test_empty_persona_scores_zero() -> None:
    pid = uuid4()
    plan = build_target_society_plan(AMBORAS_BRIEF)
    p = PersonaAuditInput(
        persona_id=pid, display_name="Empty",
        traits=tuple(
            _trait(name, "unknown", None) for name in (
                "role_or_context", "interests", "objection_patterns",
            )
        ),
        evidence_links=(),
    )
    cat = plan.stakeholder_categories[0]
    br = score_persona_against_category(
        p, cat, geography_required=False,
    )
    # No traits → low score; threshold 27 → not_relevant.
    assert br.total_score < 27
    assert classify_persona_match(br.total_score) in (
        RelevanceClassification.NOT_RELEVANT,
        RelevanceClassification.WEAKLY_RELEVANT,
    )


def test_evidence_links_increase_source_strength() -> None:
    pid = uuid4()
    s1 = uuid4(); s2 = uuid4(); s3 = uuid4(); s4 = uuid4()
    p_few = PersonaAuditInput(
        persona_id=pid, display_name="Few links",
        traits=(_trait("role_or_context", "direct", "merchant", (s1,)),),
        evidence_links=(
            _link(pid, s1, field="role_or_context", excerpt="I am a merchant"),
        ),
    )
    p_many = PersonaAuditInput(
        persona_id=pid, display_name="Many links",
        traits=(
            _trait("role_or_context", "direct", "merchant", (s1,)),
            _trait("objection_patterns", "direct", "plugin bloat", (s2,)),
            _trait("current_alternatives", "direct", "Shopify", (s3,)),
            _trait("price_sensitivity", "direct", "expensive", (s4,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context", excerpt="I am a merchant"),
            _link(pid, s2, field="objection_patterns", excerpt="plugin bloat"),
            _link(pid, s3, field="current_alternatives", excerpt="Shopify"),
            _link(pid, s4, field="price_sensitivity", excerpt="expensive"),
        ),
    )
    plan = build_target_society_plan(AMBORAS_BRIEF)
    cat = next(
        c for c in plan.stakeholder_categories
        if c.category_key == "shopify_or_platform_merchant"
    )
    br_few = score_persona_against_category(p_few, cat, geography_required=False)
    br_many = score_persona_against_category(p_many, cat, geography_required=False)
    assert br_many.source_strength >= br_few.source_strength
    assert br_many.total_score > br_few.total_score


def test_exclusion_signals_reduce_score() -> None:
    """A persona whose text contains the category's exclusion_signals
    receives a negative penalty."""
    plan = build_target_society_plan(AMBORAS_BRIEF)
    cat = next(
        c for c in plan.stakeholder_categories
        if c.category_key == "shopify_or_platform_merchant"
    )
    pid = uuid4()
    s1 = uuid4()
    # "agency-marketing voice" is in the category's exclusion_signals
    p = PersonaAuditInput(
        persona_id=pid, display_name="Agency Marketing",
        traits=(
            _trait("role_or_context", "direct",
                   "agency-marketing voice promoting agency services", (s1,)),
            _trait("objection_patterns", "direct",
                   "we help merchants with plugin bloat", (s1,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="agency-marketing voice for our agency clients"),
        ),
    )
    br = score_persona_against_category(p, cat, geography_required=False)
    assert br.exclusion_penalty < 0, (
        "Persona with exclusion signals should incur a negative penalty"
    )


def test_geography_required_rewards_geography_match() -> None:
    """When the brief requires geography, a persona with a matching
    geography_broad trait scores higher on the geography_<region>
    category than one without."""
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)  # has California
    geo_cat = next(
        c for c in plan.stakeholder_categories
        if c.category_key == "geography_california"
    )
    pid = uuid4()
    s1 = uuid4()
    p_with_geo = PersonaAuditInput(
        persona_id=pid, display_name="CA buyer",
        traits=(
            _trait("role_or_context", "direct", "grocery shopper", (s1,)),
            _trait("geography_broad", "direct", "us_california", (s1,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="I'm a California grocery shopper"),
        ),
    )
    p_no_geo = PersonaAuditInput(
        persona_id=pid, display_name="No geo",
        traits=(
            _trait("role_or_context", "direct", "grocery shopper", (s1,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="I'm a grocery shopper"),
        ),
    )
    br_geo = score_persona_against_category(p_with_geo, geo_cat, geography_required=True)
    br_no_geo = score_persona_against_category(p_no_geo, geo_cat, geography_required=True)
    assert br_geo.geography_match > br_no_geo.geography_match


def test_classify_total_score_behavior() -> None:
    assert classify_persona_match(45) == RelevanceClassification.HIGHLY_RELEVANT
    assert classify_persona_match(36) == RelevanceClassification.HIGHLY_RELEVANT
    assert classify_persona_match(35) == RelevanceClassification.RELEVANT
    assert classify_persona_match(27) == RelevanceClassification.RELEVANT
    assert classify_persona_match(18) == RelevanceClassification.WEAKLY_RELEVANT
    assert classify_persona_match(0) == RelevanceClassification.NOT_RELEVANT
    # Negative clamps to NOT_RELEVANT.
    assert classify_persona_match(-5) == RelevanceClassification.NOT_RELEVANT
