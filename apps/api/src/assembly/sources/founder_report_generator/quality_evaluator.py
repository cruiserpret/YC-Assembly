"""Phase 8.5F — deterministic 9-dimension report-quality evaluator."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from assembly.sources.founder_report_generator.schemas import (
    FounderReport,
)
from assembly.sources.run_scoped_persona_simulation import (
    scan_forecast_or_verdict_claims,
    scan_unlaunched_product_use_claims,
)


ReportReadyState = Literal[
    "READY_FOR_FRESH_END_TO_END_TEST",
    "READY_FOR_REPORT_PROMPT_FIX",
    "NOT_READY",
]


class ReportQualityEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_grounding_score: float = Field(ge=0.0, le=1.0)
    founder_actionability_score: float = Field(ge=0.0, le=1.0)
    caveat_integrity_score: float = Field(ge=0.0, le=1.0)
    anti_forecast_score: float = Field(ge=0.0, le=1.0)
    unlaunched_product_integrity_score: float = Field(ge=0.0, le=1.0)
    persona_traceability_score: float = Field(ge=0.0, le=1.0)
    evidence_traceability_score: float = Field(ge=0.0, le=1.0)
    competitor_specificity_score: float = Field(ge=0.0, le=1.0)
    readability_score: float = Field(ge=0.0, le=1.0)
    aggregate_score: float = Field(ge=0.0, le=1.0)
    ready_state: ReportReadyState
    rationale: list[str]


def _required_caveat_keywords() -> tuple[tuple[str, ...], ...]:
    return (
        ("micro-simulation", "n=7"),
        ("not a forecast",),
        ("not a market verdict",),
        ("not representative",),
        ("run-scoped",),
        ("synthetic",),
        ("unlaunched",),
        ("no direct",),
        ("amazon",),
    )


def evaluate_report_quality(
    *,
    report: FounderReport,
    rendered_markdown: str,
    product_name: str,
) -> ReportQualityEvaluation:
    """Pure deterministic scoring. Returns 9 dimensions + aggregate."""

    # 1. report_grounding_score — every objection / lever must trace
    # back to ≥1 persona role or display_name from the audience snapshot.
    persona_names = {
        p.display_name for p in report.simulated_audience_snapshot
    }
    role_names = {
        p.normalized_primary_role
        for p in report.simulated_audience_snapshot
    }
    if not (report.top_objections or report.top_persuasion_levers):
        grounding = 0.0
    else:
        n_grounded = 0
        n_total = 0
        for o in report.top_objections:
            n_total += 1
            if (
                set(o.raised_by_personas) & persona_names
                or set(o.raised_by_roles) & role_names
            ):
                n_grounded += 1
        for l in report.top_persuasion_levers:
            n_total += 1
            if set(l.likely_movable_personas) & persona_names:
                n_grounded += 1
        grounding = round(n_grounded / max(n_total, 1), 3)

    # 2. founder_actionability_score
    actionable = 0
    actionable_total = 0
    for o in report.top_objections:
        actionable_total += 1
        if o.founder_action and len(o.founder_action.strip()) >= 30:
            actionable += 1
    for l in report.top_persuasion_levers:
        actionable_total += 1
        if (
            l.suggested_founder_change
            and len(l.suggested_founder_change.strip()) >= 30
        ):
            actionable += 1
    actionability_part = (
        actionable / max(actionable_total, 1)
        if actionable_total else 0.0
    )
    if (
        report.what_to_test_next
        and report.positioning_recommendations
    ):
        actionability_extra = 1.0
    elif report.what_to_test_next or report.positioning_recommendations:
        actionability_extra = 0.5
    else:
        actionability_extra = 0.0
    actionability = round(
        0.6 * actionability_part + 0.4 * actionability_extra, 3,
    )

    # 3. caveat_integrity_score
    caveat_blob = " | ".join(report.caveats).lower()
    md_blob = (rendered_markdown or "").lower()
    hits = 0
    keywords = _required_caveat_keywords()
    for keyset in keywords:
        if all(k in caveat_blob or k in md_blob for k in keyset):
            hits += 1
    caveat = round(hits / len(keywords), 3)

    # 4. anti_forecast_score — universal scanner over the rendered
    # markdown + executive_summary + overall_reaction + recommendations.
    blob = "\n".join([
        rendered_markdown or "",
        " | ".join(report.executive_summary),
        " | ".join(report.overall_reaction),
        " | ".join(p.rationale for p in report.positioning_recommendations),
        " | ".join(t.description for t in report.what_to_test_next),
    ])
    fv = scan_forecast_or_verdict_claims(text=blob)
    anti_forecast = 1.0 if fv.is_valid else 0.0

    # 5. unlaunched_product_integrity_score
    use_v = scan_unlaunched_product_use_claims(
        text=blob, product_name=product_name,
    )
    unlaunched = 1.0 if use_v.is_valid else 0.0

    # 6. persona_traceability_score
    if not report.top_objections:
        persona_trace = 0.0
    else:
        with_traces = sum(
            1 for o in report.top_objections
            if o.raised_by_personas or o.raised_by_roles
        )
        persona_trace = round(
            with_traces / len(report.top_objections), 3,
        )

    # 7. evidence_traceability_score
    has_persona_to_evidence_map = bool(
        report.appendix.persona_to_evidence_map,
    )
    has_round_summary = bool(report.appendix.round_summary)
    has_traceability_block = bool(
        report.appendix.source_persona_traceability,
    )
    evidence_trace = round(
        (
            (1 if has_persona_to_evidence_map else 0)
            + (1 if has_round_summary else 0)
            + (1 if has_traceability_block else 0)
        ) / 3.0,
        3,
    )

    # 8. competitor_specificity_score
    if not report.competitor_comparison:
        competitor_spec = 0.0
    else:
        specific = sum(
            1 for c in report.competitor_comparison
            if (
                c.simulated_strengths
                or c.simulated_weaknesses
                or c.where_target_product_could_differentiate
            )
        )
        competitor_spec = round(
            specific / len(report.competitor_comparison), 3,
        )

    # 9. readability_score — heuristic: markdown has every required
    # section header AND avoids huge single paragraphs.
    required_headers = (
        "## 1. Executive Summary",
        "## 2. Simulated Audience Snapshot",
        "## 3. Overall Reaction",
        "## 4. Top Objections",
        "## 5. Top Persuasion Levers",
        "## 6. Competitor Comparison",
        "## 7. Proof Needed",
        "## 8. Positioning Recommendations",
        "## 9. Product / Offer Recommendations",
        "## 10. What to Test Next",
        "## 11. Caveats",
        "## 12. Appendix",
    )
    md = rendered_markdown or ""
    headers_present = sum(1 for h in required_headers if h in md)
    no_huge_para = not any(
        len(p) > 1500 for p in md.split("\n\n") if p
    )
    readability = round(
        (headers_present / len(required_headers)) * 0.7
        + (1.0 if no_huge_para else 0.5) * 0.3, 3,
    )

    weights = {
        "anti_forecast_score": 0.18,
        "unlaunched_product_integrity_score": 0.18,
        "caveat_integrity_score": 0.10,
        "founder_actionability_score": 0.12,
        "report_grounding_score": 0.10,
        "persona_traceability_score": 0.08,
        "evidence_traceability_score": 0.08,
        "competitor_specificity_score": 0.08,
        "readability_score": 0.08,
    }
    scores = {
        "anti_forecast_score": anti_forecast,
        "unlaunched_product_integrity_score": unlaunched,
        "caveat_integrity_score": caveat,
        "founder_actionability_score": actionability,
        "report_grounding_score": grounding,
        "persona_traceability_score": persona_trace,
        "evidence_traceability_score": evidence_trace,
        "competitor_specificity_score": competitor_spec,
        "readability_score": readability,
    }
    aggregate = round(
        sum(weights[k] * scores[k] for k in weights), 3,
    )

    # Ready-state rules per the 8.5F spec
    blockers: list[str] = []
    if anti_forecast < 1.0:
        blockers.append("anti_forecast_score < 1.0")
    if unlaunched < 1.0:
        blockers.append("unlaunched_product_integrity_score < 1.0")
    if caveat < 0.8:
        blockers.append("caveat_integrity_score < 0.8")
    if actionability < 0.7:
        blockers.append("founder_actionability_score < 0.7")
    if grounding < 0.7:
        blockers.append("report_grounding_score < 0.7")

    if blockers:
        if anti_forecast < 1.0 or unlaunched < 1.0:
            ready_state: ReportReadyState = "NOT_READY"
        else:
            ready_state = "READY_FOR_REPORT_PROMPT_FIX"
    else:
        ready_state = "READY_FOR_FRESH_END_TO_END_TEST"

    rationale: list[str] = []
    rationale.append(
        f"aggregate={aggregate}; "
        f"anti_forecast={anti_forecast}, "
        f"unlaunched={unlaunched}, "
        f"caveat={caveat}, "
        f"actionability={actionability}, "
        f"grounding={grounding}, "
        f"persona_trace={persona_trace}, "
        f"evidence_trace={evidence_trace}, "
        f"competitor_spec={competitor_spec}, "
        f"readability={readability}."
    )
    if blockers:
        rationale.append(f"blockers: {blockers}")

    return ReportQualityEvaluation(
        report_grounding_score=grounding,
        founder_actionability_score=actionability,
        caveat_integrity_score=caveat,
        anti_forecast_score=anti_forecast,
        unlaunched_product_integrity_score=unlaunched,
        persona_traceability_score=persona_trace,
        evidence_traceability_score=evidence_trace,
        competitor_specificity_score=competitor_spec,
        readability_score=readability,
        aggregate_score=aggregate,
        ready_state=ready_state,
        rationale=rationale,
    )
