"""Phase 12F.1 — rule-based confidence score.

The score is a weighted sum of 9 input factors, each bounded [0..1].
The final score is bounded [0..1] and HARD-CAPPED at 0.85 in 12F.1
(no profile / product combination has enough multi-product validation
to justify `high` confidence yet).

`limited_by` is ALWAYS populated with at least one entry — the cap
itself counts when no other factor is limiting.

This module does NOT import the LLM provider, the DB session, or any
network-touching surface. It's pure aggregation over the in-memory
ctx dict and the brief dict.
"""
from __future__ import annotations

import math
from typing import Any

# Hard cap in 12F.1. Bumping requires explicit operator approval after
# multi-product validation; do not silently widen.
CONFIDENCE_HARD_CAP_12F1 = 0.85

# Phase 12F.1 weak first-pass weights. Approved by operator; keep
# easy to tune by adjusting this dict — no other call site reads them.
_WEIGHTS: dict[str, float] = {
    "company_context_completeness": 0.15,
    "evidence_quality": 0.20,
    "source_audience_profile_confidence": 0.15,
    "validation_support_count": 0.10,
    "persona_diversity_health": 0.10,
    "input_ambiguity": 0.10,
    "pricing_specificity": 0.05,
    "competitor_clarity": 0.05,
    "uploaded_customer_evidence_count": 0.10,
}

# Per-launch_source confidence prior. 12E validated `hn_show_hn` on
# DocuSeal alone — single-product support → mid-low prior. `default`
# is legacy-compat and behaves like the pre-12E pipeline, so it gets
# a higher baseline. Other future profiles will get explicit entries
# as they validate.
_SOURCE_PROFILE_CONFIDENCE: dict[str, float] = {
    "default": 0.70,
    "hn_show_hn": 0.55,
}

# Category → known-validation-runs count (log-scaled). Set by operator
# as new validation cases land. Today: devtools_b2b validated on
# Opslane (1 run, in-progress); DocuSeal added one e-signature data
# point but is a different category. Keep conservative.
_CATEGORY_VALIDATION_COUNTS: dict[str, int] = {
    "devtools_b2b": 1,
}

# 12F.1 new optional context fields to score for completeness. Order
# does not matter; presence = populated AND non-empty.
_CONTEXT_FIELDS_FOR_COMPLETENESS: tuple[str, ...] = (
    "company_stage",
    "current_traction",
    "retention_or_churn_signal",
    "founder_hypothesis",
    "customer_interviews",
    "known_objections",
    "icp_segments",
    "pricing_assumptions",
    "gtm_channel",
    "competitors_with_context",
    "current_messaging",
    "decision_being_tested",
    "what_would_change_my_mind",
)


def _is_populated(value: Any) -> bool:
    """True if value is non-None, non-empty list/dict/str."""
    if value is None:
        return False
    if isinstance(value, (list, dict, tuple, set, str)):
        return len(value) > 0
    return True


def _company_context_completeness(brief: dict[str, Any]) -> float:
    populated = sum(
        1 for f in _CONTEXT_FIELDS_FOR_COMPLETENESS
        if _is_populated(brief.get(f))
    )
    return populated / len(_CONTEXT_FIELDS_FOR_COMPLETENESS)


def _evidence_quality(ctx: dict[str, Any]) -> float:
    """Use evidence-snapshot ratio when available; else fall back to a
    mid prior (0.5) so missing snapshots don't artificially deflate."""
    snap = ctx.get("_snapshot")
    if snap is None:
        return 0.5
    accepted = getattr(snap, "accepted_evidence_count", None)
    raw = getattr(snap, "raw_result_count", None)
    if not accepted or not raw or raw <= 0:
        return 0.5
    ratio = float(accepted) / float(raw)
    return max(0.0, min(1.0, ratio))


def _source_audience_profile_confidence(launch_source: str | None) -> float:
    if not launch_source:
        return _SOURCE_PROFILE_CONFIDENCE["default"]
    return _SOURCE_PROFILE_CONFIDENCE.get(launch_source, 0.30)


def _validation_support_count(brief: dict[str, Any]) -> float:
    """Log-scaled count: 0 → 0.0; 1 → ~0.5; 2 → ~0.7; 4+ → ~1.0."""
    hint = (brief.get("category_hint") or "").lower()
    matched_count = 0
    for category, count in _CATEGORY_VALIDATION_COUNTS.items():
        if category in hint:
            matched_count = max(matched_count, count)
    if matched_count <= 0:
        return 0.0
    # log(1+n)/log(5) so 4 → 1.0 cap.
    return min(1.0, math.log(1 + matched_count) / math.log(5))


def _persona_diversity_health(ctx: dict[str, Any]) -> float:
    """Map Phase 12C diversity-health output to a 0..1 score. The
    diversity-health side-effect runs AFTER report-assembly in the
    current orchestrator, so we look at quality_gates as a proxy
    when diversity_health itself isn't reachable."""
    dh = ctx.get("diversity_health") or {}
    if dh.get("all_gates_passed") is True:
        return 0.85
    if dh.get("all_gates_passed") is False:
        return 0.40
    qg = ctx.get("quality_gates") or {}
    if qg.get("all_gates_passed") is True:
        return 0.65
    if qg.get("all_gates_passed") is False:
        return 0.40
    return 0.50


def _input_ambiguity(brief: dict[str, Any]) -> float:
    """1.0 = inputs are crisp; 0.0 = inputs are vague."""
    score = 1.0
    desc = brief.get("product_description") or ""
    if len(desc) < 80:
        score -= 0.40
    elif len(desc) < 200:
        score -= 0.15
    if not (brief.get("competitors_or_alternatives") or []) and not (
        brief.get("competitors_with_context") or []
    ):
        score -= 0.20
    if not brief.get("category_hint"):
        score -= 0.10
    return max(0.0, score)


def _pricing_specificity(brief: dict[str, Any]) -> float:
    p = brief.get("pricing_assumptions")
    if p:
        tiers = p.get("tiers") if isinstance(p, dict) else None
        if tiers:
            return 1.0
        return 0.6  # structured object but no tiers
    legacy = brief.get("price_or_price_structure") or ""
    if not legacy:
        return 0.0
    text = legacy.strip().lower()
    # Heuristic: presence of "$" or a digit → 0.5; "vague" → 0.2.
    if any(t in text for t in ("vary", "varies", "tbd", "unknown")):
        return 0.2
    if any(c.isdigit() for c in text):
        return 0.5
    return 0.3


def _competitor_clarity(brief: dict[str, Any]) -> float:
    if brief.get("competitors_with_context"):
        return 1.0
    if brief.get("competitors_or_alternatives"):
        return 0.5
    return 0.0


def _uploaded_customer_evidence_count(brief: dict[str, Any]) -> float:
    """Log-scaled count of founder-supplied customer interviews +
    customer-survey artifacts. 0 → 0.0; 5 → ~0.7; 10+ → 1.0."""
    interviews = brief.get("customer_interviews") or []
    survey_artifacts = [
        a for a in (brief.get("uploaded_artifacts") or [])
        if (a.get("kind") if isinstance(a, dict) else getattr(a, "kind", None))
        in ("customer_survey_csv", "interview_notes")
    ]
    n = len(interviews) + len(survey_artifacts)
    if n <= 0:
        return 0.0
    return min(1.0, math.log(1 + n) / math.log(11))  # 10 → 1.0


def _score_to_level(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    if score >= 0.30:
        return "medium_low"
    return "low"


def _build_limited_by(
    breakdown: dict[str, float], capped: bool,
) -> list[str]:
    """Always non-empty: at minimum, the cap is a limiter."""
    limiters: list[str] = []
    if breakdown["uploaded_customer_evidence_count"] <= 0.0:
        limiters.append("no_uploaded_customer_evidence")
    if breakdown["pricing_specificity"] < 0.5:
        limiters.append("vague_pricing")
    if breakdown["competitor_clarity"] < 0.5:
        limiters.append("competitors_not_described_in_context")
    if breakdown["company_context_completeness"] < 0.4:
        limiters.append("company_context_sparse")
    if breakdown["validation_support_count"] < 0.3:
        limiters.append("category_not_yet_validated_on_real_outcomes")
    if breakdown["source_audience_profile_confidence"] < 0.6:
        limiters.append("source_audience_profile_only_weakly_calibrated")
    if breakdown["evidence_quality"] < 0.4:
        limiters.append("evidence_quality_low")
    if breakdown["input_ambiguity"] < 0.5:
        limiters.append("inputs_ambiguous_or_short")
    if breakdown["persona_diversity_health"] < 0.6:
        limiters.append("persona_diversity_gates_not_all_passing")
    if capped:
        limiters.append(
            "cap_at_0.85_until_multi_product_validation_phase_12f1"
        )
    if not limiters:
        # Defensive: should never happen given the cap clause above,
        # but the invariant is "always at least one entry".
        limiters.append(
            "no_confidence_calibration_yet_across_products"
        )
    return limiters


def _build_would_increase_if(breakdown: dict[str, float]) -> list[str]:
    suggestions: list[str] = []
    if breakdown["uploaded_customer_evidence_count"] < 0.7:
        suggestions.append("upload 5+ real customer quotes or interview notes")
    if breakdown["pricing_specificity"] < 1.0:
        suggestions.append("specify tiered pricing with included features")
    if breakdown["competitor_clarity"] < 1.0:
        suggestions.append(
            "describe competitors with `why_they_win` / `why_they_lose`"
        )
    if breakdown["company_context_completeness"] < 0.7:
        suggestions.append(
            "fill in company_stage, current_traction, decision_being_tested"
        )
    if breakdown["input_ambiguity"] < 0.8:
        suggestions.append(
            "expand product_description and name 2-3 specific competitors"
        )
    return suggestions or [
        "no founder-side input would raise confidence further — "
        "additional confidence requires multi-product validation"
    ]


def compute_confidence(
    *,
    brief: dict[str, Any],
    ctx: dict[str, Any],
    launch_source: str | None,
) -> dict[str, Any]:
    """Build the confidence block.

    Returns:
      {
        "level": str,                  # high | medium | medium_low | low
        "score": float,                # 0..1, capped at CONFIDENCE_HARD_CAP_12F1
        "score_raw": float,            # uncapped (for tuning)
        "limited_by": list[str],       # always non-empty
        "would_increase_if": list[str],
        "breakdown": {<factor>: float, ...},
        "weights": {<factor>: float, ...},
        "cap_applied": bool,
        "cap": float,
      }
    """
    breakdown: dict[str, float] = {
        "company_context_completeness": _company_context_completeness(brief),
        "evidence_quality": _evidence_quality(ctx),
        "source_audience_profile_confidence": (
            _source_audience_profile_confidence(launch_source)
        ),
        "validation_support_count": _validation_support_count(brief),
        "persona_diversity_health": _persona_diversity_health(ctx),
        "input_ambiguity": _input_ambiguity(brief),
        "pricing_specificity": _pricing_specificity(brief),
        "competitor_clarity": _competitor_clarity(brief),
        "uploaded_customer_evidence_count": (
            _uploaded_customer_evidence_count(brief)
        ),
    }
    raw_score = sum(
        breakdown[k] * _WEIGHTS[k] for k in breakdown
    )
    # Bound to [0,1] first, then apply 12F.1 hard cap.
    raw_score = max(0.0, min(1.0, raw_score))
    capped_score = min(raw_score, CONFIDENCE_HARD_CAP_12F1)
    cap_applied = capped_score < raw_score
    return {
        "level": _score_to_level(capped_score),
        "score": round(capped_score, 3),
        "score_raw": round(raw_score, 3),
        "limited_by": _build_limited_by(breakdown, cap_applied),
        "would_increase_if": _build_would_increase_if(breakdown),
        "breakdown": {k: round(v, 3) for k, v in breakdown.items()},
        "weights": dict(_WEIGHTS),
        "cap_applied": cap_applied,
        "cap": CONFIDENCE_HARD_CAP_12F1,
    }
