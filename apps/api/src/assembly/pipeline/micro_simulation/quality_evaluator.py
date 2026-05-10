"""Phase 8.4B.1 — operator-only micro-simulation output quality
evaluator.

Deterministic — NO LLM, NO network, NO DB. Takes a
`MicroSimulationResult` (or its JSON-serialized form) plus the brief
and returns a `MicroQualityReport` with per-dimension scores +
expansion-readiness recommendation.

The 9 dimensions (per the operator spec):

  1. evidence_grounding_score      — every persona used source-bound
                                     evidence excerpts
  2. competitor_comparison_score   — personas referenced named
                                     competitors / substitutes
  3. objection_specificity_score   — objections were specific, not
                                     generic
  4. founder_actionability_score   — output produced product /
                                     positioning / channel / pricing
                                     recommendations
  5. caveat_integrity_score        — mandatory caveats present and
                                     product-correct
  6. anti_fake_claim_score         — output avoided fake buyers,
                                     forecasts, percentages, success/
                                     failure verdicts
  7. stance_validity_score         — final stances were schema-valid
  8. debate_value_score            — debate caused stance movement OR
                                     surfaced useful tension
  9. coverage_score                — how many dynamic categories
                                     were represented

Expansion-readiness recommendation (closed enum):
  * not_ready
  * ready_for_prompt_fix
  * ready_for_larger_micro_sim
  * ready_for_source_expansion

The evaluator is operator-only: it surfaces signal, NOT a verdict.
The recommendation is a hint; the operator decides the next phase.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------


class QualityDimensionStatus(str, enum.Enum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


class ExpansionReadiness(str, enum.Enum):
    NOT_READY = "not_ready"
    READY_FOR_PROMPT_FIX = "ready_for_prompt_fix"
    READY_FOR_LARGER_MICRO_SIM = "ready_for_larger_micro_sim"
    READY_FOR_SOURCE_EXPANSION = "ready_for_source_expansion"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class QualityDimension:
    name: str
    score: float          # 0.0–1.0
    status: QualityDimensionStatus
    detail: str
    issues: list[str] = field(default_factory=list)


@dataclass
class MicroQualityReport:
    product_name: str
    sample_size: int
    dimensions: dict[str, QualityDimension]
    overall_score: float    # 0.0–1.0
    expansion_readiness: ExpansionReadiness
    expansion_reason: str
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Forbidden-claim regex (mirrors output_audit.py's 7-category set)
# ---------------------------------------------------------------------------


_FAKE_FORECAST_RE = re.compile(
    r"\b(?:will\s+(?:succeed|fail|dominate)|"
    r"\d{1,3}\s*%\s+(?:of|will|adopt)|"
    r"forecast(?:s|ed)?\s+(?:revenue|sales|adoption)|"
    r"verdict\s*[:=]|"
    r"market\s+success\s+probability|"
    r"build\s+it|kill\s+it|pivot\s+the\s+product|"
    r"tiny[_\s]?ready\s*(?:=|is|:)\s*(?:true|yes)|"
    r"representative\s+of\s+the\s+(?:target\s+)?market)",
    re.IGNORECASE,
)


_FAKE_BUYER_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:buyer|loyalist|reviewer)\b",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_get(d: dict[str, Any], path: str, default: Any = None) -> Any:
    """Walk a dot-path through a dict; return `default` if missing."""
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _all_persona_text(result_dict: dict[str, Any]) -> dict[str, list[str]]:
    """Collect all reasoning + objection + excerpt text per persona
    from the result's trace."""
    by_pid: dict[str, list[str]] = {}
    rounds = _safe_get(result_dict, "trace.rounds", []) or []
    for r in rounds:
        pid = r.get("persona_id", "?")
        out = by_pid.setdefault(pid, [])
        if r.get("reasoning"):
            out.append(r["reasoning"])
        for obj in (r.get("objections") or []):
            out.append(obj)
        for cit in (r.get("evidence_citations") or []):
            out.append(cit)
        if r.get("triggered_by_evidence_excerpt"):
            out.append(r["triggered_by_evidence_excerpt"])
    return by_pid


def _all_evidence_excerpts(result_dict: dict[str, Any]) -> dict[str, list[str]]:
    """Collect every persona's evidence-link excerpts (from the
    initial state)."""
    by_pid: dict[str, list[str]] = {}
    initial = _safe_get(result_dict, "persona_states_initial", []) or []
    for p in initial:
        pid = p.get("persona_id", "?")
        excerpts = list(p.get("evidence_excerpts", {}).values())
        by_pid[pid] = [e for e in excerpts if e]
    return by_pid


# ---------------------------------------------------------------------------
# Per-dimension evaluators
# ---------------------------------------------------------------------------


def _eval_evidence_grounding(
    result_dict: dict[str, Any],
) -> QualityDimension:
    """Did each persona use source-bound evidence in their reasoning?

    Heuristic: at least one of (a) `triggered_by_evidence_excerpt`
    populated on a non-baseline round, OR (b) any persona-text
    fragment contains a substring of one of the persona's evidence
    excerpts (verbatim ≥ 30 chars).
    """
    persona_text = _all_persona_text(result_dict)
    persona_excerpts = _all_evidence_excerpts(result_dict)
    grounded = 0
    not_grounded: list[str] = []

    # Also use the trace's per-round trigger field as a strong signal
    rounds = _safe_get(result_dict, "trace.rounds", []) or []
    triggers_by_pid: dict[str, int] = {}
    for r in rounds:
        if r.get("triggered_by_evidence_excerpt"):
            pid = r.get("persona_id", "?")
            triggers_by_pid[pid] = triggers_by_pid.get(pid, 0) + 1

    for pid, blob in persona_text.items():
        if triggers_by_pid.get(pid, 0) > 0:
            grounded += 1
            continue
        text_blob = "\n".join(blob).lower()
        excerpts = persona_excerpts.get(pid, [])
        # Substring match (≥ 25 char window) over any excerpt
        substring_hit = any(
            len(e) >= 25 and any(
                e[i:i + 25].lower() in text_blob
                for i in range(0, max(1, len(e) - 25), 25)
            )
            for e in excerpts
        )
        if substring_hit:
            grounded += 1
        else:
            display = "?"
            initial = _safe_get(
                result_dict, "persona_states_initial", []
            ) or []
            for p in initial:
                if p.get("persona_id") == pid:
                    display = p.get("display_name", pid)
                    break
            not_grounded.append(display)

    n = max(1, len(persona_text))
    score = grounded / n
    if score >= 0.85:
        status = QualityDimensionStatus.PASS
    elif score >= 0.5:
        status = QualityDimensionStatus.PARTIAL
    else:
        status = QualityDimensionStatus.FAIL
    return QualityDimension(
        name="evidence_grounding_score",
        score=round(score, 3),
        status=status,
        detail=(
            f"{grounded}/{n} personas reasoned from source-bound "
            "evidence excerpts."
        ),
        issues=(
            [f"persona without grounded reasoning: {n}"
             for n in not_grounded]
            if not_grounded else []
        ),
    )


def _eval_competitor_comparison(
    result_dict: dict[str, Any], competitors: list[str],
) -> QualityDimension:
    """Did personas compare the product to named competitors /
    substitutes? Looks for any competitor name in persona text."""
    competitors_lower = [c.lower() for c in competitors if c]
    persona_text = _all_persona_text(result_dict)
    comparing = 0
    for pid, blob in persona_text.items():
        text_blob = "\n".join(blob).lower()
        if any(c in text_blob for c in competitors_lower):
            comparing += 1
    n = max(1, len(persona_text))
    score = comparing / n
    if score >= 0.8:
        status = QualityDimensionStatus.PASS
    elif score >= 0.4:
        status = QualityDimensionStatus.PARTIAL
    else:
        status = QualityDimensionStatus.FAIL
    return QualityDimension(
        name="competitor_comparison_score",
        score=round(score, 3),
        status=status,
        detail=(
            f"{comparing}/{n} personas referenced at least one "
            f"named competitor / substitute "
            f"({', '.join(competitors[:5])}{'...' if len(competitors) > 5 else ''})."
        ),
    )


_GENERIC_OBJECTION_PATTERNS = (
    re.compile(r"\bnot sure\b", re.IGNORECASE),
    re.compile(r"\bI don'?t know\b", re.IGNORECASE),
    re.compile(r"\bmaybe\b", re.IGNORECASE),
    re.compile(r"\bmight (be|not be)\b", re.IGNORECASE),
    re.compile(r"\bjust (a|an) regular\b", re.IGNORECASE),
)
_SPECIFIC_OBJECTION_PATTERNS = (
    re.compile(r"\$\d+", re.IGNORECASE),
    re.compile(
        r"\b(?:caffeine|sugar|ingredient|dose|dosage|sweetener|"
        r"flavor|stack|recall|distribution|channel|availability)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d+\s*(?:mg|g|ml|oz|cans?)\b", re.IGNORECASE),
)


def _eval_objection_specificity(
    result_dict: dict[str, Any],
) -> QualityDimension:
    """Were objections specific, not generic? Heuristic: each
    objection must contain at least one 'specific' marker (named
    quantity, named ingredient/concept) and avoid 'maybe/not sure'
    hedges."""
    rounds = _safe_get(result_dict, "trace.rounds", []) or []
    total = 0
    specific = 0
    sample_generic: list[str] = []
    for r in rounds:
        for obj in (r.get("objections") or []):
            total += 1
            has_specific = any(
                p.search(obj) for p in _SPECIFIC_OBJECTION_PATTERNS
            )
            has_generic = any(
                p.search(obj) for p in _GENERIC_OBJECTION_PATTERNS
            )
            if has_specific and not has_generic:
                specific += 1
            elif not has_specific and len(sample_generic) < 3:
                sample_generic.append(obj[:120])
    if total == 0:
        return QualityDimension(
            name="objection_specificity_score",
            score=0.0,
            status=QualityDimensionStatus.FAIL,
            detail="no objections found in trace",
        )
    score = specific / total
    if score >= 0.7:
        status = QualityDimensionStatus.PASS
    elif score >= 0.4:
        status = QualityDimensionStatus.PARTIAL
    else:
        status = QualityDimensionStatus.FAIL
    return QualityDimension(
        name="objection_specificity_score",
        score=round(score, 3),
        status=status,
        detail=(
            f"{specific}/{total} objections carry specific markers "
            "(quantities, named ingredients/concepts) and avoid "
            "'maybe / not sure' hedges."
        ),
        issues=sample_generic,
    )


_ACTIONABILITY_KEYWORDS = (
    re.compile(
        r"\b(?:ingredient|caffeine|sugar|flavor|distribution|"
        r"channel|price|pricing|positioning|trial|sample|launch|"
        r"transparency|disclose|disclosur(?:e|y)|case stud(?:y|ies)|"
        r"third[-\s]party|verified|safety|recall)\b",
        re.IGNORECASE,
    ),
)


def _eval_founder_actionability(
    result_dict: dict[str, Any],
) -> QualityDimension:
    """Did the output produce founder-actionable signal? Heuristic:
    aggregate persona text must mention ≥4 distinct
    actionability-keywords categories (ingredient / channel /
    pricing / trust-signals / etc.)."""
    persona_text = _all_persona_text(result_dict)
    big_blob = "\n".join(
        "\n".join(blob) for blob in persona_text.values()
    ).lower()
    matches = set()
    for pat in _ACTIONABILITY_KEYWORDS:
        for m in pat.finditer(big_blob):
            matches.add(m.group(0).lower())
    distinct = len(matches)
    if distinct >= 6:
        status = QualityDimensionStatus.PASS
        score = 1.0
    elif distinct >= 3:
        status = QualityDimensionStatus.PARTIAL
        score = 0.6
    else:
        status = QualityDimensionStatus.FAIL
        score = 0.2
    return QualityDimension(
        name="founder_actionability_score",
        score=score,
        status=status,
        detail=(
            f"output surfaces {distinct} distinct actionability "
            f"signals (sample: {sorted(matches)[:8]})."
        ),
    )


_REQUIRED_CAVEAT_MARKERS = {
    "MICRO-TEST": re.compile(r"micro[-\s]?test", re.IGNORECASE),
    "sample-size": re.compile(r"sample[-\s]?size", re.IGNORECASE),
    "coverage-thinness": re.compile(
        r"coverage[-\s]?thinness", re.IGNORECASE,
    ),
    "not-a-forecast": re.compile(
        r"not[-\s]?a[-\s]?forecast|not a demand forecast",
        re.IGNORECASE,
    ),
}


def _eval_caveat_integrity(
    result_dict: dict[str, Any], product_name: str,
) -> QualityDimension:
    """Were mandatory caveats present? Are they product-correct
    (mention the actual product, not a leftover label)?"""
    caveats = _safe_get(result_dict, "caveats", []) or []
    enriched = _safe_get(result_dict, "enriched_caveats", caveats) or caveats
    blob = "\n".join(enriched).lower()
    issues: list[str] = []
    found = {}
    for marker, pat in _REQUIRED_CAVEAT_MARKERS.items():
        found[marker] = bool(pat.search(blob))
        if not found[marker]:
            issues.append(f"missing caveat marker: {marker}")

    # Product-correctness check: at least one caveat must reference
    # the active product (full name OR first-word). E.g. when
    # product_name='Triton Drinks', caveats containing either 'Triton
    # Drinks' or 'Triton' satisfy the check. Catches the "Amboras-
    # leftover-in-Triton-output" bug.
    product_lower = product_name.lower()
    product_first_word = product_lower.split()[0] if product_lower else ""
    product_present = (
        product_lower in blob
        or (product_first_word and product_first_word in blob)
    )
    if not product_present:
        issues.append(
            f"no caveat mentions the active product '{product_name}'"
        )

    # Anti-leftover check: caveat list must NOT mention any other
    # known launched-product brand label that is NOT a substring of
    # the active product name.
    other_brand_leakage: list[str] = []
    for forbidden in ("Amboras", "Triton"):
        forb_lower = forbidden.lower()
        # Skip if the forbidden brand IS the active product or is a
        # token of it (e.g. 'Triton' is a token of 'Triton Drinks').
        if forb_lower == product_lower or forb_lower in product_lower:
            continue
        if forb_lower in blob:
            other_brand_leakage.append(forbidden)
    if other_brand_leakage:
        issues.append(
            f"caveat leaks unrelated product brand(s): "
            f"{other_brand_leakage}"
        )

    pct = sum(1 for v in found.values() if v) / max(1, len(found))
    if pct == 1.0 and product_present and not other_brand_leakage:
        status = QualityDimensionStatus.PASS
        score = 1.0
    elif pct >= 0.5 and not other_brand_leakage:
        status = QualityDimensionStatus.PARTIAL
        score = pct
    else:
        status = QualityDimensionStatus.FAIL
        score = pct / 2 if pct else 0.0
    return QualityDimension(
        name="caveat_integrity_score",
        score=round(score, 3),
        status=status,
        detail=(
            f"{sum(1 for v in found.values() if v)}/"
            f"{len(found)} required caveat markers present; "
            f"product-name presence={product_present}; "
            f"brand-leakage={other_brand_leakage or 'none'}."
        ),
        issues=issues,
    )


def _eval_anti_fake_claim(
    result_dict: dict[str, Any], product_name: str,
) -> QualityDimension:
    """Did output avoid fake forecasts / verdicts / "X is a Triton
    buyer" labels?"""
    blob_parts: list[str] = []
    for r in (_safe_get(result_dict, "trace.rounds", []) or []):
        if r.get("reasoning"):
            blob_parts.append(r["reasoning"])
        for obj in (r.get("objections") or []):
            blob_parts.append(obj)
    for t in (_safe_get(result_dict, "trace.debate_turns", []) or []):
        if t.get("argument"):
            blob_parts.append(t["argument"])
    summary_text = _safe_get(result_dict, "summary_text", "") or ""
    if summary_text:
        blob_parts.append(summary_text)
    big_blob = "\n".join(blob_parts)

    forecast_hits = _FAKE_FORECAST_RE.findall(big_blob)
    fake_buyer_hits = []
    for m in _FAKE_BUYER_RE.finditer(big_blob):
        # Allow "competitor buyer" / generic "category buyer" by
        # checking that the match starts with the active product name
        # (which IS the violation we're detecting).
        snippet = m.group(0)
        if product_name.lower() in snippet.lower():
            fake_buyer_hits.append(snippet)

    issues: list[str] = []
    if forecast_hits:
        issues.append(
            f"forecast/verdict language found: {forecast_hits[:3]}"
        )
    if fake_buyer_hits:
        issues.append(
            f"product-buyer label found (Triton is unlaunched): "
            f"{fake_buyer_hits[:3]}"
        )
    n = len(forecast_hits) + len(fake_buyer_hits)
    if n == 0:
        score = 1.0
        status = QualityDimensionStatus.PASS
    elif n <= 2:
        score = 0.5
        status = QualityDimensionStatus.PARTIAL
    else:
        score = 0.0
        status = QualityDimensionStatus.FAIL
    return QualityDimension(
        name="anti_fake_claim_score",
        score=score,
        status=status,
        detail=(
            f"forecast/verdict hits: {len(forecast_hits)}; "
            f"fake-buyer hits: {len(fake_buyer_hits)}."
        ),
        issues=issues,
    )


_VALID_INTERNAL_STANCES = {
    "strongly_interested", "mildly_interested", "curious_hesitant",
    "confused", "skeptical", "resistant",
}
_VALID_MARKET_ENTRY_STANCES = {
    "reject", "skeptical", "curious_but_unconvinced",
    "willing_to_try_once", "likely_repeat_buyer",
}


def _eval_stance_validity(
    result_dict: dict[str, Any],
) -> QualityDimension:
    final_states = (
        _safe_get(result_dict, "persona_states_final", []) or []
    )
    invalid: list[str] = []
    for p in final_states:
        s = p.get("current_stance")
        if s not in _VALID_INTERNAL_STANCES:
            invalid.append(f"{p.get('display_name', '?')}: {s!r}")
    me = (
        _safe_get(result_dict, "final_stances_market_entry", []) or []
    )
    me_invalid: list[str] = []
    for fs in me:
        if fs.get("market_entry_stance") not in _VALID_MARKET_ENTRY_STANCES:
            me_invalid.append(
                f"{fs.get('display_name', '?')}: "
                f"{fs.get('market_entry_stance')!r}"
            )
    n = max(1, len(final_states))
    valid = n - len(invalid)
    score = valid / n
    issues = invalid + me_invalid
    if score == 1.0 and not me_invalid:
        status = QualityDimensionStatus.PASS
    elif score >= 0.8:
        status = QualityDimensionStatus.PARTIAL
    else:
        status = QualityDimensionStatus.FAIL
    return QualityDimension(
        name="stance_validity_score",
        score=round(score, 3),
        status=status,
        detail=(
            f"{valid}/{n} personas have schema-valid internal stance; "
            f"market-entry mapping invalid: {len(me_invalid)}."
        ),
        issues=issues,
    )


def _eval_debate_value(
    result_dict: dict[str, Any],
) -> QualityDimension:
    debate_turns = (
        _safe_get(result_dict, "trace.debate_turns", []) or []
    )
    if not debate_turns:
        return QualityDimension(
            name="debate_value_score",
            score=0.0,
            status=QualityDimensionStatus.FAIL,
            detail="no debate turns in trace",
        )
    moved = sum(
        1 for t in debate_turns
        if t.get("target_stance_before") != t.get("target_stance_after")
    )
    failed = sum(
        1 for t in debate_turns if not t.get("output_audit_passed")
    )
    n = len(debate_turns)
    movement_rate = moved / n
    audit_pass_rate = (n - failed) / n
    # Score combines movement and audit-pass rates
    score = 0.5 * audit_pass_rate + 0.5 * (
        1.0 if movement_rate > 0 else 0.5  # 0.5 partial credit when
        # debate ran cleanly but didn't move stance — still useful
        # tension can be revealed even without movement
    )
    if audit_pass_rate == 1.0 and (movement_rate > 0 or n >= 2):
        status = QualityDimensionStatus.PASS
    elif audit_pass_rate >= 0.5:
        status = QualityDimensionStatus.PARTIAL
    else:
        status = QualityDimensionStatus.FAIL
    return QualityDimension(
        name="debate_value_score",
        score=round(score, 3),
        status=status,
        detail=(
            f"{n} debate turns; {moved} caused stance movement; "
            f"{n - failed}/{n} passed audit."
        ),
    )


def _eval_coverage(
    result_dict: dict[str, Any], total_plan_categories: int,
) -> QualityDimension:
    """How many distinct dynamic categories were represented?"""
    initial = (
        _safe_get(result_dict, "persona_states_initial", []) or []
    )
    distinct = len({
        p.get("matched_category_key") for p in initial
        if p.get("matched_category_key")
    })
    total = max(1, total_plan_categories)
    pct = distinct / total
    if pct >= 0.4:
        status = QualityDimensionStatus.PASS
        score = 1.0
    elif pct >= 0.15:
        status = QualityDimensionStatus.PARTIAL
        score = pct / 0.4
    else:
        status = QualityDimensionStatus.FAIL
        score = pct / 0.4
    return QualityDimension(
        name="coverage_score",
        score=round(score, 3),
        status=status,
        detail=(
            f"{distinct}/{total} dynamic-plan categories represented "
            f"({pct:.0%})."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level evaluator
# ---------------------------------------------------------------------------


def evaluate_micro_simulation_quality(
    *,
    result_dict: dict[str, Any],
    product_name: str,
    competitors: list[str],
    total_plan_categories: int,
) -> MicroQualityReport:
    """Run all 9 dimensions + compute expansion-readiness recommendation."""
    sample_size = (
        _safe_get(result_dict, "persona_count", 0)
        or len(_safe_get(result_dict, "persona_states_final", []) or [])
    )

    dims = {
        "evidence_grounding_score": _eval_evidence_grounding(result_dict),
        "competitor_comparison_score": _eval_competitor_comparison(
            result_dict, competitors,
        ),
        "objection_specificity_score": _eval_objection_specificity(
            result_dict,
        ),
        "founder_actionability_score": _eval_founder_actionability(
            result_dict,
        ),
        "caveat_integrity_score": _eval_caveat_integrity(
            result_dict, product_name,
        ),
        "anti_fake_claim_score": _eval_anti_fake_claim(
            result_dict, product_name,
        ),
        "stance_validity_score": _eval_stance_validity(result_dict),
        "debate_value_score": _eval_debate_value(result_dict),
        "coverage_score": _eval_coverage(
            result_dict, total_plan_categories,
        ),
    }

    overall = round(
        sum(d.score for d in dims.values()) / len(dims), 3,
    )

    # Decide expansion readiness from per-dimension statuses
    crit_fail = [
        n for n, d in dims.items()
        if d.status == QualityDimensionStatus.FAIL
        and n in (
            "anti_fake_claim_score",
            "stance_validity_score",
            "caveat_integrity_score",
        )
    ]
    issues_overall: list[str] = []
    for d in dims.values():
        issues_overall.extend(d.issues)

    recommendations: list[str] = []
    if crit_fail:
        readiness = ExpansionReadiness.NOT_READY
        reason = (
            f"critical dimension failed: {crit_fail}. Must fix before "
            "any larger run."
        )
        recommendations.append(
            "Fix critical dimensions before any larger micro-simulation."
        )
    elif (
        dims["objection_specificity_score"].status
        == QualityDimensionStatus.FAIL
        or dims["evidence_grounding_score"].status
        == QualityDimensionStatus.FAIL
    ):
        readiness = ExpansionReadiness.READY_FOR_PROMPT_FIX
        reason = (
            "evidence-grounding or objection-specificity weak; "
            "prompt-quality fix recommended before larger run."
        )
        recommendations.append(
            "Tune round prompts to push more specific objections."
        )
    elif dims["coverage_score"].status == QualityDimensionStatus.FAIL:
        readiness = ExpansionReadiness.READY_FOR_SOURCE_EXPANSION
        reason = (
            "category coverage thin; broader source ingestion will "
            "yield more inclusion-eligible personas across the "
            "dynamic-plan categories."
        )
        recommendations.append(
            "Expand source coverage (Brave / Reddit / YouTube / "
            "Amazon-review) before scaling persona count."
        )
    else:
        readiness = ExpansionReadiness.READY_FOR_LARGER_MICRO_SIM
        reason = (
            "all critical dimensions pass; the audience produces "
            "evidence-grounded, founder-actionable signal at "
            "current scale. A larger micro-simulation is mechanically "
            "sound, though source-coverage expansion remains a "
            "prerequisite for any society-scale run."
        )
        recommendations.append(
            "Expand to a larger micro-simulation (e.g. all 21 "
            "production-retrieved included personas) for broader "
            "category coverage."
        )
        recommendations.append(
            "Source-coverage expansion (Brave / Reddit / YouTube) is "
            "still required before any society-scale (50+) run."
        )

    return MicroQualityReport(
        product_name=product_name,
        sample_size=sample_size,
        dimensions=dims,
        overall_score=overall,
        expansion_readiness=readiness,
        expansion_reason=reason,
        issues=issues_overall,
        recommendations=recommendations,
    )


def report_to_dict(report: MicroQualityReport) -> dict[str, Any]:
    """Serialize for audit JSON. Closed-enum values are stringified."""
    return {
        "product_name": report.product_name,
        "sample_size": report.sample_size,
        "overall_score": report.overall_score,
        "expansion_readiness": report.expansion_readiness.value,
        "expansion_reason": report.expansion_reason,
        "dimensions": {
            name: {
                "name": d.name,
                "score": d.score,
                "status": d.status.value,
                "detail": d.detail,
                "issues": list(d.issues),
            }
            for name, d in report.dimensions.items()
        },
        "issues": list(report.issues),
        "recommendations": list(report.recommendations),
    }


__all__ = [
    "ExpansionReadiness",
    "MicroQualityReport",
    "QualityDimension",
    "QualityDimensionStatus",
    "evaluate_micro_simulation_quality",
    "report_to_dict",
]
