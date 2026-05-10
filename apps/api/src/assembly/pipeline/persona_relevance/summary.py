"""Phase 8.2F.7 — audit report formatter + next-step recommendation.

`format_audit_report(...)` returns a human-readable text report for the
operator console; `recommend_next_step(...)` returns a closed-enum
A / B / C recommendation based on the audit's aggregate signal.

No DB access; pure functions over `AggregateAuditResult`.
"""
from __future__ import annotations

import enum
from typing import Final

from assembly.pipeline.persona_relevance.auditor import (
    AggregateAuditResult,
    PersonaRelevanceScore,
)
from assembly.pipeline.persona_relevance.rubric import (
    CLASSIFICATION_THRESHOLDS,
    RelevanceClassification,
    SCORE_FIELDS,
    STAKEHOLDER_CATEGORIES,
    TOTAL_MAX,
    StakeholderCategory,
)


class NextStepRecommendation(str, enum.Enum):
    PROCEED_TO_TINY_SIMULATION = "A_proceed_to_tiny_simulation_test"
    BROADEN_INGESTION_FIRST = "B_broaden_ingestion_first"
    FIX_EXTRACTION_OR_RELEVANCE_RULES = "C_fix_extraction_or_relevance_rules"


# Tunable thresholds for the recommendation logic. Kept conservative.
_MIN_HIGHLY_RELEVANT_FOR_SIM: Final[int] = 5
_MIN_RELEVANT_OR_HIGHER_FOR_SIM: Final[int] = 8
_MIN_DISTINCT_CATEGORIES_FOR_SIM: Final[int] = 4
_DUPLICATE_FINGERPRINT_FRACTION_FOR_REDUNDANCY: Final[float] = 0.35


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


def recommend_next_step(result: AggregateAuditResult) -> NextStepRecommendation:
    """A / B / C decision logic.

    A) PROCEED_TO_TINY_SIMULATION — at least 5 highly-relevant + at
       least 8 relevant-or-higher AND at least 4 distinct stakeholder
       categories matched.
    C) FIX_EXTRACTION_OR_RELEVANCE_RULES — extraction looks broken
       (e.g. >50% of personas in classification `not_relevant` despite
       being built from strong-signal records, OR average source-
       strength score < 1.5).
    B) BROADEN_INGESTION_FIRST — default fallthrough; not enough
       coverage / diversity yet.
    """
    if result.personas_audited == 0:
        return NextStepRecommendation.BROADEN_INGESTION_FIRST

    n_high = result.classification_counts.get(
        RelevanceClassification.HIGHLY_RELEVANT, 0,
    )
    n_rel_plus = (
        n_high + result.classification_counts.get(
            RelevanceClassification.RELEVANT, 0,
        )
    )
    n_not = result.classification_counts.get(
        RelevanceClassification.NOT_RELEVANT, 0,
    )
    pct_not = n_not / result.personas_audited
    avg_source = result.average_scores.get("source_strength_score", 0.0)
    distinct_categories = sum(
        1 for c in STAKEHOLDER_CATEGORIES
        if result.matched_categories.get(c, 0) > 0
    )

    # C — extraction looks broken (fix scoring/extraction first).
    if pct_not >= 0.5 or avg_source < 1.5:
        return NextStepRecommendation.FIX_EXTRACTION_OR_RELEVANCE_RULES

    # A — tiny sim test threshold met.
    if (
        n_high >= _MIN_HIGHLY_RELEVANT_FOR_SIM
        and n_rel_plus >= _MIN_RELEVANT_OR_HIGHER_FOR_SIM
        and distinct_categories >= _MIN_DISTINCT_CATEGORIES_FOR_SIM
    ):
        return NextStepRecommendation.PROCEED_TO_TINY_SIMULATION

    # Default — broaden ingestion.
    return NextStepRecommendation.BROADEN_INGESTION_FIRST


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------


def format_audit_report(
    result: AggregateAuditResult,
    *,
    top_n: int = 5,
    weak_n: int = 5,
) -> str:
    """Return a human-readable audit report. No side effects."""
    lines: list[str] = []
    bar = "=" * 64
    lines.append(bar)
    lines.append("Phase 8.2F.7 — Persona Relevance Audit")
    lines.append(bar)
    lines.append(f"personas_audited:     {result.personas_audited}")
    lines.append("")
    lines.append("Classification counts:")
    for c in (
        RelevanceClassification.HIGHLY_RELEVANT,
        RelevanceClassification.RELEVANT,
        RelevanceClassification.WEAKLY_RELEVANT,
        RelevanceClassification.NOT_RELEVANT,
    ):
        n = result.classification_counts.get(c, 0)
        lo = CLASSIFICATION_THRESHOLDS[c]
        # Inclusive upper bound:
        sorted_thresholds = sorted(CLASSIFICATION_THRESHOLDS.items(), key=lambda x: x[1])
        idx = next(i for i, (cc, _) in enumerate(sorted_thresholds) if cc == c)
        hi = (
            sorted_thresholds[idx + 1][1] - 1
            if idx + 1 < len(sorted_thresholds)
            else TOTAL_MAX
        )
        lines.append(f"  {c.value}: {n} (range {lo}–{hi})")
    lines.append("")
    lines.append("Average scores by category:")
    for f in SCORE_FIELDS:
        v = result.average_scores.get(f, 0.0)
        lines.append(f"  {f}: {v:.2f} / 5")
    lines.append("")

    # Top N
    sorted_personas = sorted(
        result.per_persona, key=lambda s: s.total_score, reverse=True,
    )
    lines.append(f"Top {top_n} strongest personas:")
    for s in sorted_personas[:top_n]:
        lines.append(_format_persona_line(s, why=True))
    lines.append("")

    # Weak / not relevant
    weak = [
        s for s in sorted_personas
        if s.classification in (
            RelevanceClassification.NOT_RELEVANT,
            RelevanceClassification.WEAKLY_RELEVANT,
        )
    ]
    lines.append(
        f"Weak / not_relevant personas ({len(weak)} total; showing up to {weak_n}):"
    )
    if not weak:
        lines.append("  <none>")
    else:
        for s in weak[:weak_n]:
            lines.append(_format_persona_line(s, why=False, reason=True))
    lines.append("")

    # Redundancy
    lines.append("Redundancy / fingerprint analysis:")
    if result.duplicate_fingerprints:
        for fp, n in sorted(
            result.duplicate_fingerprints.items(),
            key=lambda kv: kv[1], reverse=True,
        ):
            lines.append(f"  fingerprint {fp[:60]!r}…  ×{n}")
    else:
        lines.append("  no exact-fingerprint duplicates")
    pct_redundant = (
        sum(result.duplicate_fingerprints.values())
        / max(result.personas_audited, 1)
    )
    if pct_redundant >= _DUPLICATE_FINGERPRINT_FRACTION_FOR_REDUNDANCY:
        lines.append(
            f"  WARNING: {pct_redundant:.0%} of personas share a "
            "fingerprint with at least one other — batch is likely "
            "viewpoint-redundant (consider broader ingestion)."
        )
    lines.append("")

    # Stakeholder coverage
    lines.append("Stakeholder category coverage:")
    for cat in STAKEHOLDER_CATEGORIES:
        n = result.matched_categories.get(cat, 0)
        marker = "✗" if n == 0 else "✓"
        lines.append(f"  {marker} {cat.value}: {n}")
    lines.append("")
    if result.missing_categories:
        lines.append("Missing stakeholder categories:")
        for cat in result.missing_categories:
            lines.append(f"  - {cat.value}")
    else:
        lines.append("Missing stakeholder categories: <none — all covered>")
    lines.append("")

    # Recommendation
    rec = recommend_next_step(result)
    lines.append(f"Recommendation: {rec.value}")
    lines.append(_explain_recommendation(rec, result))
    lines.append(bar)
    return "\n".join(lines)


def _format_persona_line(
    s: PersonaRelevanceScore, *, why: bool, reason: bool = False,
) -> str:
    head = (
        f"  {s.display_name:>14}  total={s.total_score:>2}/{TOTAL_MAX}  "
        f"[{s.classification.value}]  links={s.matched_keyword_counts.get('source_strength_meta', 0) % 10}  "
        f"persona_id={s.persona_id}"
    )
    detail = []
    if why:
        cats = ", ".join(c.value for c in s.matched_stakeholder_categories) or "(no category match)"
        detail.append(f"     categories: {cats}")
        detail.append("     " + " | ".join(s.rationale[:6]))
    if reason:
        # Reason for weakness — pick the lowest sub-scores to surface
        sub_scores = {
            "role_context": s.role_context_score,
            "pain_points": s.pain_point_score,
            "alts": s.current_alternative_score,
            "price": s.price_budget_score,
            "trust": s.trust_objection_score,
            "source_strength": s.source_strength_score,
        }
        worst = sorted(sub_scores.items(), key=lambda kv: kv[1])[:3]
        detail.append("     weak axes: " + ", ".join(
            f"{k}={v}/5" for k, v in worst
        ))
    return head + ("\n" + "\n".join(detail) if detail else "")


def _explain_recommendation(
    rec: NextStepRecommendation, result: AggregateAuditResult,
) -> str:
    n_high = result.classification_counts.get(
        RelevanceClassification.HIGHLY_RELEVANT, 0,
    )
    n_rel_plus = (
        n_high + result.classification_counts.get(
            RelevanceClassification.RELEVANT, 0,
        )
    )
    n_not = result.classification_counts.get(
        RelevanceClassification.NOT_RELEVANT, 0,
    )
    distinct_cats = sum(
        1 for c in STAKEHOLDER_CATEGORIES
        if result.matched_categories.get(c, 0) > 0
    )
    if rec is NextStepRecommendation.PROCEED_TO_TINY_SIMULATION:
        return (
            f"  Reasoning: {n_high} highly-relevant, {n_rel_plus} relevant+, "
            f"{distinct_cats} stakeholder categories covered — sufficient "
            "for a tiny simulation test on the relevant subset."
        )
    if rec is NextStepRecommendation.FIX_EXTRACTION_OR_RELEVANCE_RULES:
        avg_source = result.average_scores.get("source_strength_score", 0.0)
        return (
            f"  Reasoning: extraction looks degraded — "
            f"{n_not} personas classified not_relevant, avg source_strength="
            f"{avg_source:.2f}/5. Tighten the prompt or scoring before more "
            "ingestion."
        )
    return (
        f"  Reasoning: only {n_high} highly-relevant + {n_rel_plus} relevant+, "
        f"{distinct_cats} stakeholder categories covered. Need broader "
        "human-signal ingestion (Phase 8.2F.6) before a meaningful sim run."
    )
