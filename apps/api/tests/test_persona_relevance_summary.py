"""Phase 8.2F.7 — summary tests (pure)."""
from __future__ import annotations

from uuid import UUID, uuid4

from assembly.pipeline.persona_relevance.auditor import (
    AggregateAuditResult,
    PersonaRelevanceScore,
)
from assembly.pipeline.persona_relevance.rubric import (
    RelevanceClassification,
    SCORE_FIELDS,
    StakeholderCategory,
    TOTAL_MAX,
)
from assembly.pipeline.persona_relevance.summary import (
    NextStepRecommendation,
    format_audit_report,
    recommend_next_step,
)


def _score(
    *,
    pid: UUID | None = None,
    name: str = "X",
    role: int = 5, pain: int = 5, alt: int = 5, price: int = 5, trust: int = 5,
    source: int = 5, human: int = 5, diversity: int = 5, sim: int = 5,
    cats: tuple[StakeholderCategory, ...] = (),
) -> PersonaRelevanceScore:
    total = role + pain + alt + price + trust + source + human + diversity + sim
    from assembly.pipeline.persona_relevance.rubric import classify_total_score
    return PersonaRelevanceScore(
        persona_id=pid or uuid4(),
        display_name=name,
        role_context_score=role, pain_point_score=pain,
        current_alternative_score=alt, price_budget_score=price,
        trust_objection_score=trust, source_strength_score=source,
        human_signal_score=human, viewpoint_diversity_score=diversity,
        simulation_usefulness_score=sim,
        total_score=total,
        classification=classify_total_score(total),
        matched_stakeholder_categories=cats,
        rationale=("test",),
    )


def _result(scores: list[PersonaRelevanceScore], categories=None) -> AggregateAuditResult:
    classification_counts = {c: 0 for c in RelevanceClassification}
    for s in scores:
        classification_counts[s.classification] += 1
    avg = {f: round(sum(getattr(s, f) for s in scores) / max(len(scores), 1), 2)
           for f in SCORE_FIELDS}
    matched = {c: 0 for c in StakeholderCategory}
    if categories is None:
        categories = []
    for s in scores:
        for c in s.matched_stakeholder_categories:
            matched[c] += 1
    missing = tuple(c for c in StakeholderCategory if matched[c] == 0)
    return AggregateAuditResult(
        personas_audited=len(scores),
        classification_counts=classification_counts,
        average_scores=avg,
        per_persona=tuple(scores),
        matched_categories=matched,
        missing_categories=missing,
        duplicate_fingerprints={},
    )


# ---------------------------------------------------------------------------
# Recommendation logic
# ---------------------------------------------------------------------------


def test_recommendation_proceed_when_thresholds_met() -> None:
    """5 highly-relevant + 5 relevant + 4 distinct categories → A."""
    cats = (
        StakeholderCategory.SHOPIFY_MERCHANT_PLUGIN_FATIGUE,
        StakeholderCategory.PRICE_SENSITIVE_SMB,
        StakeholderCategory.AGENCY_DEPENDENT_MERCHANT,
        StakeholderCategory.AI_SKEPTICAL_OPERATOR,
    )
    scores = []
    # 5 highly_relevant, each carrying a distinct category
    for i, c in enumerate(cats[:4] + cats[:1]):
        scores.append(_score(role=5, pain=5, alt=5, price=5, trust=5,
                             source=5, human=5, diversity=5, sim=5,
                             cats=(c,)))
    # 4 relevant
    for _ in range(4):
        scores.append(_score(role=4, pain=4, alt=3, price=4, trust=3,
                             source=4, human=4, diversity=3, sim=3,
                             cats=(cats[0],)))
    rec = recommend_next_step(_result(scores))
    assert rec is NextStepRecommendation.PROCEED_TO_TINY_SIMULATION


def test_recommendation_fix_when_extraction_degraded() -> None:
    """≥ 50% not_relevant → C."""
    scores = [
        _score(role=0, pain=0, alt=0, price=0, trust=0,
               source=0, human=0, diversity=1, sim=0)
        for _ in range(5)
    ]
    rec = recommend_next_step(_result(scores))
    assert rec is NextStepRecommendation.FIX_EXTRACTION_OR_RELEVANCE_RULES


def test_recommendation_broaden_when_thin_pool() -> None:
    """Not enough highly-relevant + categories → B."""
    scores = [
        _score(role=4, pain=3, alt=2, price=2, trust=2,
               source=3, human=3, diversity=3, sim=2,
               cats=(StakeholderCategory.SHOPIFY_MERCHANT_PLUGIN_FATIGUE,))
        for _ in range(3)
    ]
    rec = recommend_next_step(_result(scores))
    assert rec is NextStepRecommendation.BROADEN_INGESTION_FIRST


def test_recommendation_with_no_personas_is_broaden() -> None:
    rec = recommend_next_step(_result([]))
    assert rec is NextStepRecommendation.BROADEN_INGESTION_FIRST


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def test_format_report_includes_classification_counts_and_top_personas() -> None:
    scores = [
        _score(name="Top",  role=5, pain=5, alt=5, price=5, trust=5,
               source=5, human=5, diversity=5, sim=5,
               cats=(StakeholderCategory.SHOPIFY_MERCHANT_PLUGIN_FATIGUE,)),
        _score(name="Weak", role=1, pain=0, alt=0, price=0, trust=0,
               source=1, human=1, diversity=2, sim=0),
    ]
    text = format_audit_report(_result(scores))
    assert "personas_audited" in text
    assert "Classification counts" in text
    assert "Top 5 strongest personas" in text
    assert "Top" in text  # name appears
    assert "Weak" in text
    assert "Recommendation:" in text
    assert "Stakeholder category coverage:" in text


def test_format_report_lists_missing_categories() -> None:
    """A batch covering 0 stakeholder categories must list every category as missing."""
    scores = [
        _score(role=0, pain=0, alt=0, price=0, trust=0,
               source=0, human=0, diversity=1, sim=0)
    ]
    text = format_audit_report(_result(scores))
    assert "Missing stakeholder categories" in text
    # Every closed-enum category should appear in the missing list.
    for c in StakeholderCategory:
        assert c.value in text


def test_format_report_sorts_top_personas_descending() -> None:
    """Top-N section lists strongest personas first."""
    cats = (StakeholderCategory.SHOPIFY_MERCHANT_PLUGIN_FATIGUE,)
    scores = [
        _score(name="Aaa", role=1, pain=1, alt=1, price=1, trust=1,
               source=1, human=1, diversity=2, sim=1, cats=cats),
        _score(name="Bbb", role=5, pain=5, alt=5, price=5, trust=5,
               source=5, human=5, diversity=5, sim=5, cats=cats),
        _score(name="Ccc", role=3, pain=3, alt=3, price=3, trust=3,
               source=3, human=3, diversity=3, sim=3, cats=cats),
    ]
    text = format_audit_report(_result(scores), top_n=3)
    # Bbb (top) should appear before Ccc, Ccc before Aaa in the Top
    # section of the report. Locate the Top section and check ordering.
    top_section = text.split("Top 3 strongest personas")[1].split("Weak / not_relevant")[0]
    bbb_pos = top_section.find("Bbb")
    ccc_pos = top_section.find("Ccc")
    aaa_pos = top_section.find("Aaa")
    assert 0 <= bbb_pos < ccc_pos < aaa_pos
