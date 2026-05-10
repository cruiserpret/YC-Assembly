"""Phase 8.2F.7 — auditor tests (pure)."""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
    audit_personas,
    score_persona,
)
from assembly.pipeline.persona_relevance.rubric import (
    RelevanceClassification,
    StakeholderCategory,
    TOTAL_MAX,
)


def _trait(field_name: str, support_level: str, value: str | None,
           confidence: float = 0.8, source_ids: tuple[UUID, ...] = ()) -> TraitView:
    return TraitView(
        field_name=field_name,
        support_level=support_level,
        value=value,
        confidence=confidence,
        source_ids=source_ids,
    )


def _link(persona_id: UUID, source_record_id: UUID, *, excerpt: str,
          field: str, kind: str = "direct",
          likely: bool | None = True) -> EvidenceLinkView:
    return EvidenceLinkView(
        persona_id=persona_id,
        source_record_id=source_record_id,
        contribution_kind=kind,
        contribution_field=field,
        excerpt=excerpt,
        source_likely_human_signal=likely,
    )


# ---------------------------------------------------------------------------
# Highly relevant Shopify-merchant-with-plugin-fatigue persona scores high
# ---------------------------------------------------------------------------


def test_highly_relevant_shopify_merchant_persona_scores_high() -> None:
    pid = uuid4()
    s1 = uuid4(); s2 = uuid4(); s3 = uuid4(); s4 = uuid4()
    p = PersonaAuditInput(
        persona_id=pid,
        display_name="Tatum G.",
        traits=(
            _trait("role_or_context", "direct",
                   "New Shopify store owner and ecommerce entrepreneur",
                   source_ids=(s1,)),
            _trait("objection_patterns", "direct",
                   "Frustrated with plugin bloat and too many apps; "
                   "fed up with monthly fees", source_ids=(s2,)),
            _trait("current_alternatives", "direct",
                   "Shopify apps Klaviyo Oberlo agency", source_ids=(s3,)),
            _trait("price_sensitivity", "direct",
                   "high; cumulative monthly fee plus plugin fees expensive",
                   source_ids=(s4,)),
            _trait("trust_triggers", "inferred",
                   "wants transparency and final control over branding",
                   confidence=0.7, source_ids=(s2,)),
            _trait("buying_constraints", "direct",
                   "small business owner with budget concerns",
                   source_ids=(s4,)),
            _trait("interests", "direct",
                   "ecommerce store management and branding",
                   source_ids=(s1,)),
            _trait("communication_style", "unknown", None, confidence=0.0),
            _trait("influence_signals", "unknown", None, confidence=0.0),
            _trait("geography_broad", "unknown", None, confidence=0.0),
        ),
        evidence_links=(
            _link(pid, s1, field="role_or_context",
                  excerpt="I'm a Shopify merchant doing $30k/month"),
            _link(pid, s2, field="objection_patterns",
                  excerpt="my plugin stack is overwhelming and I'm fed up"),
            _link(pid, s3, field="current_alternatives",
                  excerpt="using Klaviyo for email and Oberlo for products"),
            _link(pid, s4, field="price_sensitivity",
                  excerpt="cumulative monthly fee plus plugin fees is expensive"),
        ),
    )
    s = score_persona(p)
    assert s.classification in (
        RelevanceClassification.HIGHLY_RELEVANT,
        RelevanceClassification.RELEVANT,
    )
    assert s.role_context_score >= 4
    assert s.pain_point_score >= 3
    assert s.current_alternative_score >= 3
    assert s.price_budget_score >= 3
    assert s.source_strength_score >= 4
    # Bound: total ≤ 45.
    assert s.total_score <= TOTAL_MAX
    # Should match plugin-fatigue + price-sensitive-SMB stakeholder cats.
    cats = set(s.matched_stakeholder_categories)
    assert StakeholderCategory.SHOPIFY_MERCHANT_PLUGIN_FATIGUE in cats
    assert StakeholderCategory.PRICE_SENSITIVE_SMB in cats


# ---------------------------------------------------------------------------
# Generic / off-topic persona scores low
# ---------------------------------------------------------------------------


def test_generic_offtopic_persona_scores_low() -> None:
    pid = uuid4()
    p = PersonaAuditInput(
        persona_id=pid,
        display_name="Off Topic",
        traits=(
            _trait("role_or_context", "inferred",
                   "casual blog reader interested in cooking",
                   confidence=0.5),
            _trait("interests", "inferred",
                   "cooking and gardening", confidence=0.5),
            _trait("communication_style", "unknown", None, confidence=0.0),
            _trait("price_sensitivity", "unknown", None, confidence=0.0),
            _trait("buying_constraints", "unknown", None, confidence=0.0),
            _trait("trust_triggers", "unknown", None, confidence=0.0),
            _trait("objection_patterns", "unknown", None, confidence=0.0),
            _trait("current_alternatives", "unknown", None, confidence=0.0),
            _trait("influence_signals", "unknown", None, confidence=0.0),
            _trait("geography_broad", "unknown", None, confidence=0.0),
        ),
        evidence_links=(),  # zero direct/inferred links
    )
    s = score_persona(p)
    assert s.classification in (
        RelevanceClassification.NOT_RELEVANT,
        RelevanceClassification.WEAKLY_RELEVANT,
    )
    # Pain / current-alternative / price sub-scores must all be 0.
    assert s.pain_point_score == 0
    assert s.current_alternative_score == 0
    assert s.price_budget_score == 0


# ---------------------------------------------------------------------------
# Persona with no evidence_links scores low on source_strength
# ---------------------------------------------------------------------------


def test_persona_with_no_evidence_links_scores_low_source_strength() -> None:
    pid = uuid4()
    p = PersonaAuditInput(
        persona_id=pid,
        display_name="Empty Source",
        traits=(_trait("role_or_context", "unknown", None),) * 3,
        evidence_links=(),
    )
    s = score_persona(p)
    assert s.source_strength_score == 0
    assert s.human_signal_score == 0


# ---------------------------------------------------------------------------
# Unknown traits do not inflate any sub-score
# ---------------------------------------------------------------------------


def test_unknown_traits_do_not_inflate_scores() -> None:
    pid = uuid4()
    p = PersonaAuditInput(
        persona_id=pid,
        display_name="All Unknown",
        traits=tuple(
            _trait(name, "unknown", None, confidence=0.0)
            for name in (
                "role_or_context", "interests", "buying_constraints",
                "trust_triggers", "current_alternatives",
                "communication_style", "influence_signals",
                "price_sensitivity", "objection_patterns", "geography_broad",
            )
        ),
        evidence_links=(),
    )
    s = score_persona(p)
    assert s.role_context_score == 0
    assert s.pain_point_score == 0
    assert s.current_alternative_score == 0
    assert s.price_budget_score == 0
    assert s.trust_objection_score == 0
    assert s.source_strength_score == 0


# ---------------------------------------------------------------------------
# Direct trait with source_ids increases source-strength
# ---------------------------------------------------------------------------


def test_direct_traits_with_evidence_increase_source_strength() -> None:
    pid = uuid4()
    s_ids = [uuid4() for _ in range(5)]
    traits = tuple(
        _trait(name, "direct", "value", source_ids=(sid,))
        for name, sid in zip(
            ("role_or_context", "objection_patterns", "current_alternatives",
             "price_sensitivity", "trust_triggers"), s_ids,
        )
    )
    links = tuple(
        _link(pid, sid, field=field, excerpt="x")
        for field, sid in zip(
            ("role_or_context", "objection_patterns", "current_alternatives",
             "price_sensitivity", "trust_triggers"), s_ids,
        )
    )
    p = PersonaAuditInput(
        persona_id=pid, display_name="Strong", traits=traits, evidence_links=links,
    )
    s = score_persona(p)
    assert s.source_strength_score == 5


# ---------------------------------------------------------------------------
# Audit aggregator
# ---------------------------------------------------------------------------


def test_audit_aggregates_classifications_and_categories() -> None:
    pid_a = uuid4()
    pid_b = uuid4()
    s1 = uuid4(); s2 = uuid4()
    pa = PersonaAuditInput(
        persona_id=pid_a, display_name="Strong",
        traits=(
            _trait("role_or_context", "direct",
                   "shopify merchant store owner", source_ids=(s1,)),
            _trait("objection_patterns", "direct",
                   "plugin bloat too many apps", source_ids=(s2,)),
            _trait("current_alternatives", "direct",
                   "shopify apps klaviyo", source_ids=(s2,)),
            _trait("price_sensitivity", "direct",
                   "expensive monthly fees", source_ids=(s2,)),
            _trait("trust_triggers", "inferred",
                   "concerned about brand control",
                   confidence=0.7, source_ids=(s1,)),
        ),
        evidence_links=(
            _link(pid_a, s1, field="role_or_context",
                  excerpt="I'm a shopify merchant doing $30k/month"),
            _link(pid_a, s2, field="objection_patterns",
                  excerpt="plugin bloat is overwhelming"),
        ),
    )
    pb = PersonaAuditInput(
        persona_id=pid_b, display_name="Weak",
        traits=(_trait("role_or_context", "unknown", None),) * 3,
        evidence_links=(),
    )
    result = audit_personas([pa, pb])
    assert result.personas_audited == 2
    assert result.classification_counts.get(RelevanceClassification.NOT_RELEVANT, 0) >= 1
    # Averages computed and bounded.
    for v in result.average_scores.values():
        assert 0.0 <= v <= 5.0
    # Strong persona matches at least one stakeholder category.
    assert StakeholderCategory.SHOPIFY_MERCHANT_PLUGIN_FATIGUE in result.matched_categories


# ---------------------------------------------------------------------------
# Audit does not mutate inputs (TraitView is frozen; sanity check the API)
# ---------------------------------------------------------------------------


def test_audit_does_not_mutate_inputs() -> None:
    pid = uuid4()
    t = _trait("role_or_context", "direct", "shopify merchant", source_ids=(uuid4(),))
    p = PersonaAuditInput(
        persona_id=pid, display_name="X", traits=(t,) * 3, evidence_links=(),
    )
    score_persona(p)
    audit_personas([p])
    # TraitView is frozen — any mutation would raise; the call returning
    # cleanly is the proof.
    assert t.value == "shopify merchant"
