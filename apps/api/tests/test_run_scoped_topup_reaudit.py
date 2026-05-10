"""Phase 8.2I — re-audit before/after comparison tests (pure)."""
from __future__ import annotations

from uuid import uuid4

from assembly.pipeline.audience_retrieval import (
    NextStepRecommendation,
    retrieve_personas_for_target_society,
)
from assembly.pipeline.audience_retrieval.schemas import (
    CategoryCoverageLabel,
)
from assembly.pipeline.persona_relevance.auditor import (
    EvidenceLinkView,
    PersonaAuditInput,
    TraitView,
)
from assembly.pipeline.run_scoped_topup import compare_before_after
from assembly.pipeline.target_society import (
    AMBORAS_BRIEF,
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


def _audience(personas):
    plan = build_target_society_plan(AMBORAS_BRIEF)
    return retrieve_personas_for_target_society(
        brief=AMBORAS_BRIEF, plan=plan, personas=personas,
    )


def test_before_zero_after_one_persona_increases_matched_delta() -> None:
    before = _audience([])
    after = _audience([_amboras_persona()])
    r = compare_before_after(before=before, after=after)
    assert r.matched_delta >= 1
    assert r.before_matched_count == 0
    assert r.after_matched_count >= 1


def test_per_category_delta_records_changes() -> None:
    before = _audience([])
    after = _audience([_amboras_persona()])
    r = compare_before_after(before=before, after=after)
    # Find at least one category with delta > 0.
    flips = [c for c in r.per_category if c.delta > 0]
    assert flips, "expected at least one category with positive delta"


def test_remaining_missing_high_priority_surfaced() -> None:
    """Even after adding one Amboras-style persona, several
    high-priority categories remain missing → the remaining_missing
    list is non-empty."""
    before = _audience([])
    after = _audience([_amboras_persona()])
    r = compare_before_after(before=before, after=after)
    assert r.remaining_missing_categories  # non-empty


def test_no_change_keeps_readiness_consistent() -> None:
    """Same audience-retrieval before & after → all readiness flags
    must be identical."""
    a = _audience([])
    r = compare_before_after(before=a, after=a)
    assert r.before_tiny_ready == r.after_tiny_ready
    assert r.before_small_ready == r.after_small_ready
    assert r.before_serious_ready == r.after_serious_ready
    assert r.matched_delta == 0


def test_recommendation_values_propagate() -> None:
    before = _audience([])
    after = _audience([_amboras_persona()])
    r = compare_before_after(before=before, after=after)
    assert isinstance(r.next_step_recommendation_before, NextStepRecommendation)
    assert isinstance(r.next_step_recommendation_after, NextStepRecommendation)


def test_new_caveats_only_lists_post_topup_additions() -> None:
    """Caveats present in `before` but also in `after` are not new."""
    before = _audience([])
    after = _audience([_amboras_persona()])
    r = compare_before_after(before=before, after=after)
    for nc in r.new_caveats:
        assert nc not in before.warnings_and_caveats
        assert nc in after.warnings_and_caveats
