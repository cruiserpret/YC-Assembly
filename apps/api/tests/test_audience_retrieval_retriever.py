"""Phase 8.2H — retriever (matcher) tests (pure)."""
from __future__ import annotations

from uuid import uuid4

from assembly.pipeline.audience_retrieval import (
    match_personas_to_categories,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.persona_relevance.rubric import RelevanceClassification
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


def _smartphone_persona() -> PersonaAuditInput:
    pid = uuid4()
    s1, s2 = uuid4(), uuid4()
    return PersonaAuditInput(
        persona_id=pid,
        display_name="Phoenix R.",
        traits=(
            _trait("role_or_context", "direct",
                   "current iPhone user considering upgrade", (s1,)),
            _trait("objection_patterns", "direct",
                   "incremental upgrade not worth it; AI features are gimmicks", (s2,)),
            _trait("current_alternatives", "direct",
                   "keep existing iPhone or Samsung Galaxy S25", (s2,)),
            _trait("price_sensitivity", "direct",
                   "carrier upgrade cost is too expensive", (s2,)),
            _trait("trust_triggers", "inferred",
                   "concerned about AI privacy on device", (s1,)),
            _trait("interests", "direct",
                   "smartphone spec comparison", (s2,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="I'm a current iPhone user looking at the next upgrade"),
            _link(pid, s2, field="objection_patterns",
                  excerpt="incremental upgrade not worth it; AI features are gimmicks"),
        ),
    )


# ---------------------------------------------------------------------------
# Amboras: relevant personas retrieved
# ---------------------------------------------------------------------------


def test_amboras_persona_matches_amboras_plan() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    persona = _amboras_persona()
    matched, excluded = match_personas_to_categories(
        plan=plan, personas=[persona],
    )
    assert len(matched) == 1
    m = matched[0]
    assert m.matched_category_key == "shopify_or_platform_merchant"
    assert m.classification in (
        RelevanceClassification.RELEVANT,
        RelevanceClassification.HIGHLY_RELEVANT,
    )
    assert excluded == []


# ---------------------------------------------------------------------------
# Cross-domain isolation: an Amboras-shape persona is excluded from a
# water-bottle plan.
# ---------------------------------------------------------------------------


def test_amboras_persona_excluded_from_water_bottle_plan() -> None:
    plan = build_target_society_plan(WATER_BOTTLE_BRIEF)
    persona = _amboras_persona()
    matched, excluded = match_personas_to_categories(
        plan=plan, personas=[persona],
    )
    assert len(matched) == 0
    assert len(excluded) == 1


def test_amboras_persona_excluded_from_iphone_plan() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    persona = _amboras_persona()
    matched, excluded = match_personas_to_categories(
        plan=plan, personas=[persona],
    )
    assert len(matched) == 0
    assert len(excluded) == 1


def test_amboras_persona_excluded_from_halal_plan() -> None:
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    persona = _amboras_persona()
    matched, excluded = match_personas_to_categories(
        plan=plan, personas=[persona],
    )
    assert len(matched) == 0
    assert len(excluded) == 1


# ---------------------------------------------------------------------------
# A smartphone-shape persona matches the iPhone plan.
# ---------------------------------------------------------------------------


def test_smartphone_persona_matches_iphone_plan() -> None:
    plan = build_target_society_plan(IPHONE_17_BRIEF)
    persona = _smartphone_persona()
    matched, excluded = match_personas_to_categories(
        plan=plan, personas=[persona],
    )
    assert len(matched) == 1
    assert matched[0].matched_category_key in (
        "current_product_user",
        "competitor_user",
        "upgrade_fatigued_buyer",
        "ai_feature_skeptic",
        "current_alternative_samsung_galaxy_s25",
    )


# ---------------------------------------------------------------------------
# Empty pool / sensitive caveats
# ---------------------------------------------------------------------------


def test_empty_persona_pool_returns_no_matches_no_excluded() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    matched, excluded = match_personas_to_categories(
        plan=plan, personas=[],
    )
    assert matched == []
    assert excluded == []


def test_sensitive_caveat_propagates_to_persona_match() -> None:
    """Halal-financing plan has sensitivity_or_compliance_notes on
    every category — when a persona matches one of those categories,
    the caveat must propagate to the PersonaMatch."""
    plan = build_target_society_plan(HALAL_FINANCING_BRIEF)
    pid = uuid4()
    s1 = uuid4()
    persona = PersonaAuditInput(
        persona_id=pid,
        display_name="Compliance Buyer",
        traits=(
            _trait("role_or_context", "direct",
                   "homebuyer with compliance concerns", (s1,)),
            _trait("objection_patterns", "direct",
                   "is this actually compliant", (s1,)),
            _trait("current_alternatives", "direct",
                   "Guidance Residential conventional mortgage", (s1,)),
            _trait("trust_triggers", "direct",
                   "wants certified compliance", (s1,)),
            _trait("price_sensitivity", "inferred",
                   "rate vs incumbent mortgage", (s1,)),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt=("I'm a homebuyer looking for compliance options "
                           "vs Guidance Residential")),
        ),
    )
    matched, excluded = match_personas_to_categories(
        plan=plan, personas=[persona],
    )
    if matched:
        # If matched, caveats must include the compliance note.
        assert matched[0].caveats, "sensitive caveats must propagate"


# ---------------------------------------------------------------------------
# Domain map propagation
# ---------------------------------------------------------------------------


def test_domain_by_record_id_populates_persona_match_domains() -> None:
    plan = build_target_society_plan(AMBORAS_BRIEF)
    persona = _amboras_persona()
    domain_map = {
        e.source_record_id: f"d{i}.example.test"
        for i, e in enumerate(persona.evidence_links)
    }
    matched, _ = match_personas_to_categories(
        plan=plan, personas=[persona],
        domain_by_record_id=domain_map,
    )
    assert matched
    assert sorted(matched[0].source_domains) == sorted(domain_map.values())
