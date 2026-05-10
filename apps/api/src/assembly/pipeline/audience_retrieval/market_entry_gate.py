"""Phase 8.4A.3 — market-entry inclusion gate.

Layered on top of the existing scorer + classification thresholds.
The gate does NOT change scores, weights, or thresholds. It applies
an additional anchor-evidence requirement before a persona is
permitted into CORE_RELEVANT or ADJACENT_RELEVANT under
MARKET_ENTRY_RELEVANCE.

Rules:

  * If the persona's best-fit `total_score` would land at
    CORE_RELEVANT (≥ 27), the gate REQUIRES at least one
    market-entry anchor (per `detect_market_entry_anchors`). If no
    anchor → downgrade to EXCLUDED with reason
    `no_market_entry_anchor`.

  * If the score would land at ADJACENT_RELEVANT (18..26), the gate
    requires (a) at least one anchor AND (b) at least one
    evidence-link excerpt that contains an anchor term (so the
    anchor is grounded in real source evidence, not just a trait
    label). If anchor is missing → EXCLUDED. If anchor is present
    but no excerpt-grounded → EXCLUDED with reason
    `insufficient_anchor_evidence`.

  * If the score would land at EXCLUDED (< 18), the gate is a
    no-op — already EXCLUDED.

  * For NON-market-entry plans (classic launched-product path), the
    gate is also a no-op — the anchor mechanism is only meaningful
    when the plan has `competitor_user_*` / `use_case_*` /
    `objection_*` / etc. categories from the dynamic planner.

The gate is opt-in: callers (e.g. the replay script, future audience-
retrieval pipeline integrations) explicitly invoke
`apply_market_entry_inclusion_gate`. It is NOT auto-wired into the
existing `retrieve_personas_for_target_society` surface in this phase.
"""
from __future__ import annotations

from dataclasses import dataclass

from assembly.pipeline.audience_retrieval.anchor_detector import (
    AnchorReport,
    detect_market_entry_anchors,
)
from assembly.pipeline.audience_retrieval.inclusion_tier import (
    InclusionTier,
    classify_inclusion_tier_from_score,
)
from assembly.pipeline.persona_relevance.auditor import PersonaAuditInput
from assembly.pipeline.target_society.schemas import TargetSocietyPlan


# Closed enum of downgrade reasons (string literals for JSON-friendliness).
GATE_REASON_PASS = "pass"
GATE_REASON_NO_ANCHOR = "no_market_entry_anchor"
GATE_REASON_INSUFFICIENT_EVIDENCE = "insufficient_anchor_evidence"
GATE_REASON_BELOW_THRESHOLD = "below_inclusion_threshold"


@dataclass(frozen=True)
class GateResult:
    """The result of running the inclusion gate on one persona."""

    base_tier: InclusionTier
    final_tier: InclusionTier
    reason: str
    anchor_report: AnchorReport
    score: int


def _is_market_entry_plan(plan: TargetSocietyPlan) -> bool:
    """A plan is market-entry-shaped if at least one of its categories
    uses the dynamic-planner naming scheme (`competitor_user_*` /
    `use_case_*` / `objection_*` / etc.). The classic CPG / SaaS
    template path doesn't produce these prefixes."""
    for cat in plan.stakeholder_categories:
        if (
            cat.category_key.startswith("competitor_user_")
            or cat.category_key.startswith("substitute_user_")
            or cat.category_key.startswith("use_case_")
            or cat.category_key.startswith("objection_")
            or cat.category_key.startswith("buyer_type_")
        ):
            return True
    return False


def apply_market_entry_inclusion_gate(
    *,
    persona: PersonaAuditInput,
    plan: TargetSocietyPlan,
    score: int,
) -> GateResult:
    """Apply the anchor gate. Returns a `GateResult` carrying:
      * base_tier: what the score alone would say
      * final_tier: what the gate says after the anchor check
      * reason: one of the closed reason codes
      * anchor_report: the underlying anchor detection
      * score: the integer total_score
    """
    base_tier = classify_inclusion_tier_from_score(score)
    anchor_report = detect_market_entry_anchors(persona, plan)

    # Below threshold — gate is a no-op.
    if base_tier == InclusionTier.EXCLUDED:
        return GateResult(
            base_tier=base_tier,
            final_tier=base_tier,
            reason=GATE_REASON_BELOW_THRESHOLD,
            anchor_report=anchor_report,
            score=score,
        )

    # Classic-template plan → gate doesn't apply.
    if not _is_market_entry_plan(plan):
        return GateResult(
            base_tier=base_tier,
            final_tier=base_tier,
            reason=GATE_REASON_PASS,
            anchor_report=anchor_report,
            score=score,
        )

    # CORE_RELEVANT — require any anchor.
    if base_tier == InclusionTier.CORE_RELEVANT:
        if not anchor_report.has_anchor:
            return GateResult(
                base_tier=base_tier,
                final_tier=InclusionTier.EXCLUDED,
                reason=GATE_REASON_NO_ANCHOR,
                anchor_report=anchor_report,
                score=score,
            )
        return GateResult(
            base_tier=base_tier,
            final_tier=base_tier,
            reason=GATE_REASON_PASS,
            anchor_report=anchor_report,
            score=score,
        )

    # ADJACENT_RELEVANT — require anchor AND excerpt-grounded.
    if base_tier == InclusionTier.ADJACENT_RELEVANT:
        if not anchor_report.has_anchor:
            return GateResult(
                base_tier=base_tier,
                final_tier=InclusionTier.EXCLUDED,
                reason=GATE_REASON_NO_ANCHOR,
                anchor_report=anchor_report,
                score=score,
            )
        # The anchor must show up in at least ONE evidence-link
        # excerpt — not only in trait values. This prevents a thin
        # trait label (e.g. "Red Bull user" without source-bound
        # excerpt evidence) from sneaking in via ADJACENT.
        if not anchor_report.anchor_evidence_excerpts:
            return GateResult(
                base_tier=base_tier,
                final_tier=InclusionTier.EXCLUDED,
                reason=GATE_REASON_INSUFFICIENT_EVIDENCE,
                anchor_report=anchor_report,
                score=score,
            )
        return GateResult(
            base_tier=base_tier,
            final_tier=base_tier,
            reason=GATE_REASON_PASS,
            anchor_report=anchor_report,
            score=score,
        )

    # Defensive: should not reach here.
    return GateResult(  # pragma: no cover
        base_tier=base_tier,
        final_tier=base_tier,
        reason=GATE_REASON_PASS,
        anchor_report=anchor_report,
        score=score,
    )


__all__ = [
    "GATE_REASON_BELOW_THRESHOLD",
    "GATE_REASON_INSUFFICIENT_EVIDENCE",
    "GATE_REASON_NO_ANCHOR",
    "GATE_REASON_PASS",
    "GateResult",
    "apply_market_entry_inclusion_gate",
]
