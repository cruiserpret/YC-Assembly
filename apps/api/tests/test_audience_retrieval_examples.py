"""Phase 8.2H — end-to-end example tests (pure)."""
from __future__ import annotations

from uuid import uuid4

from assembly.pipeline.audience_retrieval import (
    NextStepRecommendation,
    retrieve_personas_for_target_society,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
    HALAL_FINANCING_BRIEF,
    IPHONE_17_BRIEF,
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


def _link(persona_id, source_id, *, excerpt, field):
    return EvidenceLinkView(
        persona_id=persona_id,
        source_record_id=source_id,
        contribution_kind="direct",
        contribution_field=field,
        excerpt=excerpt,
        source_likely_human_signal=True,
    )


def _amboras_persona() -> PersonaAuditInput:
    pid = uuid4()
    s1, s2, s3, s4 = uuid4(), uuid4(), uuid4(), uuid4()
    return PersonaAuditInput(
        persona_id=pid,
        display_name="Amboras Persona",
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


# ---------------------------------------------------------------------------
# Amboras: an Amboras-shape persona retrieves at least one match.
# ---------------------------------------------------------------------------


def test_amboras_example_retrieves_amboras_persona() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=AMBORAS_BRIEF, plan=plan, personas=[persona],
    )
    assert len(result.matched_personas) == 1
    m = result.matched_personas[0]
    assert m.matched_category_key in (
        "shopify_or_platform_merchant",
        "dtc_founder_brand_control",
        "ai_skeptical_operator",
    )


# ---------------------------------------------------------------------------
# Wrong-product briefs do NOT reuse Amboras personas.
# ---------------------------------------------------------------------------


def test_water_bottle_example_does_not_reuse_amboras_persona() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    amboras_persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=WATER_BOTTLE_BRIEF, plan=plan, personas=[amboras_persona],
    )
    assert len(result.matched_personas) == 0
    # And every category coverage is missing → top-up recs emitted.
    assert all(c.matched_total == 0 for c in result.category_coverage)
    assert len(result.topup_recommendations) >= 4
    assert result.next_step_recommendation in (
        NextStepRecommendation.RUN_TOPUP_INGESTION_FIRST,
        NextStepRecommendation.HOLD_FOR_COMPLIANCE_REVIEW,
    )


def test_iphone_example_does_not_reuse_amboras_persona() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    amboras_persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=IPHONE_17_BRIEF, plan=plan, personas=[amboras_persona],
    )
    assert len(result.matched_personas) == 0
    assert len(result.topup_recommendations) >= 4
    assert result.next_step_recommendation == (
        NextStepRecommendation.RUN_TOPUP_INGESTION_FIRST
    )


def test_halal_financing_example_includes_sensitive_caveats() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    amboras_persona = _amboras_persona()  # irrelevant persona
    result = retrieve_personas_for_target_society(
        brief=HALAL_FINANCING_BRIEF, plan=plan, personas=[amboras_persona],
    )
    # Sensitive caveats must surface in warnings_and_caveats.
    assert any(
        "sensitive" in w.lower() or "compliance" in w.lower()
        for w in result.warnings_and_caveats
    )
    # Top-up recs must be marked compliance-review.
    assert all(
        t.requires_extra_compliance_review
        for t in result.topup_recommendations
    )
    # Halal-financing should land on E (HOLD_FOR_COMPLIANCE_REVIEW).
    assert result.next_step_recommendation == (
        NextStepRecommendation.HOLD_FOR_COMPLIANCE_REVIEW
    )


# ---------------------------------------------------------------------------
# Sanity: empty pool is handled cleanly across all 4 example briefs.
# ---------------------------------------------------------------------------


def test_all_4_examples_handle_empty_persona_pool_cleanly() -> None:
    for brief in (
        AMBORAS_BRIEF, WATER_BOTTLE_BRIEF, IPHONE_17_BRIEF, HALAL_FINANCING_BRIEF,
    ):
        plan = build_target_society_plan(brief)
        result = retrieve_personas_for_target_society(
            brief=brief, plan=plan, personas=[],
        )
        assert len(result.matched_personas) == 0
        assert len(result.excluded_personas) == 0
        # Every category needs top-up.
        assert len(result.topup_recommendations) == len(plan.stakeholder_categories)
        # Tiny / small / serious all blocked.
        assert result.readiness_by_mode.tiny_ready is False
        assert result.readiness_by_mode.small_ready is False
        assert result.readiness_by_mode.serious_ready is False


# ---------------------------------------------------------------------------
# Generalization invariant: matched-category sets across briefs differ.
# ---------------------------------------------------------------------------


def test_amboras_persona_matched_category_keys_are_amboras_shaped() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    persona = _amboras_persona()
    result = retrieve_personas_for_target_society(
        brief=AMBORAS_BRIEF, plan=plan, personas=[persona],
    )
    # Confirm matched category is in the commerce family's set.
    commerce_keys = {
        "shopify_or_platform_merchant",
        "dtc_founder_brand_control",
        "agency_dependent_merchant",
        "ai_skeptical_operator",
        "nontechnical_founder",
        "current_alternative_shopify_magic",
        "current_alternative_conversion_ai_tool",
        "geography_us_canada",
    }
    for m in result.matched_personas:
        assert m.matched_category_key in commerce_keys
