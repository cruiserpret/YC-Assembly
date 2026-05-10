"""Phase 8.2H — deterministic persona × stakeholder-category scorer.

Pure functions. No DB, no LLM, no network.

Scoring axes (each 0–5 unless noted):

  1. role_context_match
  2. pain_objection_match
  3. current_alternative_match
  4. price_budget_match
  5. trust_trigger_match
  6. category_specific_match
  7. geography_match            (only counts when brief has geography)
  8. source_strength
  9. exclusion_penalty          (0 to -10; subtracted)

Total range = -10 to ~45 (exclusion can drag a persona below 0).
Classification thresholds match Phase 8.2F.7:
  highly_relevant >= 36
  relevant       >= 27
  weakly_relevant >= 18
  not_relevant    < 18

The scorer takes a `PersonaAuditInput` (the same view used by Phase
8.2F.7) and a `StakeholderCategory` (from Phase 8.2G).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from assembly.pipeline.audience_retrieval.weights import (
    UNIFORM_WEIGHTS,
    apply_weights_to_breakdown,
)
from assembly.pipeline.persona_relevance.auditor import PersonaAuditInput, TraitView
from assembly.pipeline.persona_relevance.rubric import (
    RelevanceClassification,
    classify_total_score,
)
from assembly.pipeline.target_society.schemas import StakeholderCategory


# ---------------------------------------------------------------------------
# Scoring result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryScoreBreakdown:
    role_context_match: int
    pain_objection_match: int
    current_alternative_match: int
    price_budget_match: int
    trust_trigger_match: int
    category_specific_match: int
    geography_match: int
    source_strength: int
    exclusion_penalty: int   # negative or zero
    total_score: int
    matched_signals: tuple[str, ...]
    missing_signals: tuple[str, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_WORD_BOUNDARY = r"(?<![A-Za-z0-9])"
_WORD_BOUNDARY_END = r"(?![A-Za-z0-9])"


def _build_keyword_regex(keywords: Iterable[str]) -> re.Pattern[str] | None:
    """Build a case-insensitive word-boundary alternation. Returns
    None when the keyword list is empty (so the caller can short-
    circuit to score=0)."""
    cleaned = [re.escape(k.lower()) for k in keywords if k and k.strip()]
    if not cleaned:
        return None
    return re.compile(
        _WORD_BOUNDARY + r"(?:" + "|".join(cleaned) + r")" + _WORD_BOUNDARY_END,
        re.IGNORECASE,
    )


def _supported(t: TraitView) -> bool:
    return (
        t.support_level in ("direct", "inferred")
        and t.value is not None
        and t.value.strip() != ""
    )


def _trait_text_for(p: PersonaAuditInput, field_name: str) -> str:
    out: list[str] = []
    for t in p.traits:
        if t.field_name == field_name and _supported(t):
            out.append(t.value or "")
    return "\n".join(out).lower()


def _all_text(p: PersonaAuditInput) -> str:
    parts: list[str] = []
    for t in p.traits:
        if _supported(t):
            parts.append(t.value or "")
            if t.rationale:
                parts.append(t.rationale)
    for e in p.evidence_links:
        if e.excerpt:
            parts.append(e.excerpt)
    return "\n".join(parts).lower()


def _count_hits(text: str, pattern: re.Pattern[str] | None) -> int:
    if not text or pattern is None:
        return 0
    return len(pattern.findall(text))


def _clamp(n: int, lo: int = 0, hi: int = 5) -> int:
    return max(lo, min(hi, n))


# ---------------------------------------------------------------------------
# Per-axis scoring
# ---------------------------------------------------------------------------


def _score_role_context(p: PersonaAuditInput, c: StakeholderCategory) -> tuple[int, list[str], list[str]]:
    role_text = _trait_text_for(p, "role_or_context")
    matched: list[str] = []
    missing: list[str] = []

    # Build keyword set: the category's display_name + first sentence of
    # description + inclusion signals.
    keywords: list[str] = []
    keywords.extend(_extract_role_words(c.display_name))
    keywords.extend(_extract_role_words(c.description))
    keywords.extend(_extract_role_words(s) for s in c.inclusion_signals)
    flat: list[str] = []
    for k in keywords:
        if isinstance(k, list):
            flat.extend(k)
        else:
            flat.append(k)
    pat = _build_keyword_regex(flat)
    hits = _count_hits(role_text, pat)
    if hits == 0:
        missing.append("role_or_context did not match category role keywords")
        return 0, matched, missing
    # 1 hit → 4 (role-context match is high-signal); 2+ hits → 5.
    score = 4 if hits == 1 else 5
    matched.append(f"role_or_context~{c.category_key}({hits} keyword hits)")
    return score, matched, missing


_ROLE_NOUNS_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:merchant|founder|operator|owner|seller|buyer|"
    r"shopper|consumer|user|reader|borrower|investor|enthusiast|skeptic|"
    r"shopkeeper|client|customer)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _extract_role_words(text: str) -> list[str]:
    """Pull role-shape nouns out of a string for keyword matching."""
    if not text:
        return []
    out: list[str] = list({m.group(0).lower() for m in _ROLE_NOUNS_RE.finditer(text)})
    # Add bigrams that are commonly in display_names (e.g. "shopify
    # merchant", "DTC founder") — split on whitespace and pull the
    # role-noun-prefixed words.
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]+", text)
    lowered = [t.lower() for t in tokens]
    common_modifiers = {
        "shopify", "ecommerce", "dtc", "premium", "skeptical", "agency",
        "freelancer", "compliance", "regulated", "homebuyer",
        "homebuyers",
    }
    for i, tok in enumerate(lowered):
        if tok in common_modifiers and i + 1 < len(lowered):
            out.append(f"{tok} {lowered[i + 1]}")
    return out


def _score_pain_objection(
    p: PersonaAuditInput, c: StakeholderCategory,
) -> tuple[int, list[str], list[str]]:
    text = (
        _trait_text_for(p, "objection_patterns")
        + "\n" + _trait_text_for(p, "buying_constraints")
        + "\n" + _trait_text_for(p, "interests")
    )
    keywords = list(c.likely_pains) + list(c.likely_objections)
    pat = _build_keyword_regex(_split_phrases(keywords))
    hits = _count_hits(text, pat)
    matched: list[str] = []
    missing: list[str] = []
    if hits == 0:
        missing.append("no pain/objection match")
        return 0, matched, missing
    # 1 hit → 3, 2 hits → 4, 3+ hits → 5.
    if hits >= 3:
        score = 5
    elif hits == 2:
        score = 4
    else:
        score = 3
    matched.append(f"pain/objection match ({hits} hits)")
    return score, matched, missing


def _split_phrases(phrases: Iterable[str]) -> list[str]:
    """Take a list of phrases and emit both whole phrases and salient
    word tokens (≥4 chars). Increases recall against persona text."""
    out: list[str] = []
    for p in phrases:
        if not p:
            continue
        out.append(p)
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9'-]+", p):
            if len(tok) >= 4:
                out.append(tok)
    # Dedup while preserving order.
    seen: set[str] = set()
    out_dedup: list[str] = []
    for t in out:
        tl = t.lower()
        if tl in seen:
            continue
        seen.add(tl)
        out_dedup.append(t)
    return out_dedup


def _score_current_alternative(
    p: PersonaAuditInput, c: StakeholderCategory,
) -> tuple[int, list[str], list[str]]:
    text = (
        _trait_text_for(p, "current_alternatives")
        + "\n" + _trait_text_for(p, "interests")
    )
    excerpts = "\n".join(e.excerpt or "" for e in p.evidence_links).lower()
    keywords = _split_phrases(c.likely_current_alternatives)
    pat = _build_keyword_regex(keywords)
    hits_field = _count_hits(text, pat)
    hits_excerpt = _count_hits(excerpts, pat)
    composite = hits_field * 2 + hits_excerpt
    score = _clamp(min(composite, 5))
    matched: list[str] = []
    missing: list[str] = []
    if hits_field or hits_excerpt:
        matched.append(
            f"current_alternative match ({hits_field} field + "
            f"{hits_excerpt} excerpt)"
        )
    else:
        missing.append("no current_alternative match")
    return score, matched, missing


def _score_price_budget(
    p: PersonaAuditInput, c: StakeholderCategory,
) -> tuple[int, list[str], list[str]]:
    text = (
        _trait_text_for(p, "price_sensitivity")
        + "\n" + _trait_text_for(p, "buying_constraints")
    )
    excerpts = "\n".join(e.excerpt or "" for e in p.evidence_links).lower()
    price_pat = re.compile(
        r"(?<![A-Za-z0-9])(?:price|pricing|expensive|cheap|cost|costly|"
        r"afford|budget|fee|monthly fee|tier|plan|\$|"
        r"value for money|too much|overpriced|rip[- ]?off)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    hits = _count_hits(text, price_pat) * 2 + _count_hits(excerpts, price_pat)
    has_price_trait = any(
        t.field_name == "price_sensitivity" and _supported(t) for t in p.traits
    )
    score = _clamp(min(hits, 5))
    if has_price_trait and score < 2:
        score = 2
    matched: list[str] = []
    missing: list[str] = []
    if score:
        matched.append(f"price/budget signal ({hits} hits)")
    else:
        missing.append("no price/budget signal")
    return score, matched, missing


def _score_trust_trigger(
    p: PersonaAuditInput, c: StakeholderCategory,
) -> tuple[int, list[str], list[str]]:
    text = (
        _trait_text_for(p, "trust_triggers")
        + "\n" + _trait_text_for(p, "objection_patterns")
    )
    pat = re.compile(
        r"(?<![A-Za-z0-9])(?:trust|skeptical|concern|control|guarantee|"
        r"transparent|proof|credibility|reliability|lock[- ]?in|"
        r"data privacy|privacy)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    hits = _count_hits(text, pat)
    has_trust_trait = any(
        t.field_name == "trust_triggers" and _supported(t) for t in p.traits
    )
    matched: list[str] = []
    missing: list[str] = []
    if hits == 0 and not has_trust_trait:
        missing.append("no trust/objection signal")
        return 0, matched, missing
    # has trust trait OR 1 hit → 3; 2 hits → 4; 3+ hits → 5.
    if hits >= 3:
        score = 5
    elif hits == 2:
        score = 4
    else:
        score = 3
    matched.append(f"trust signal ({hits} hits)")
    return score, matched, missing


def _score_category_specific(
    p: PersonaAuditInput, c: StakeholderCategory,
) -> tuple[int, list[str], list[str]]:
    """Match the persona's text against the category's
    `source_query_themes` + `inclusion_signals` keywords. This is the
    catch-all for category-shape overlap."""
    text = _all_text(p)
    keywords = _split_phrases(
        list(c.source_query_themes) + list(c.inclusion_signals),
    )
    pat = _build_keyword_regex(keywords)
    hits = _count_hits(text, pat)
    score = _clamp(min(hits, 5))
    matched: list[str] = []
    missing: list[str] = []
    if score:
        matched.append(f"category-specific match ({hits} hits)")
    else:
        missing.append("no category-specific theme match")
    return score, matched, missing


def _score_geography(
    p: PersonaAuditInput, c: StakeholderCategory, geography_required: bool,
) -> tuple[int, list[str], list[str]]:
    """Geography contributes ONLY when scoring against a geography_<region>
    stakeholder category — and only when the plan explicitly requires
    geography. For all other categories, geography is neutral (axis
    skipped, score=0, no penalty surfaced as a missing-signal)."""
    if not c.category_key.startswith("geography_"):
        return 0, [], []
    if not geography_required:
        return 0, [], []
    geo_text = _trait_text_for(p, "geography_broad")
    if not geo_text:
        return 0, [], ["no geography_broad on persona but brief has geography"]
    geo_keywords: list[str] = []
    geo_keywords.append(c.category_key.replace("geography_", ""))
    geo_keywords.extend(_split_phrases([c.display_name]))
    pat = _build_keyword_regex(geo_keywords)
    hits = _count_hits(geo_text, pat)
    score = _clamp(min(hits + 2, 5)) if hits else 1
    matched: list[str] = []
    if hits:
        matched.append(f"geography match ({hits} hits)")
    return score, matched, []


def _score_source_strength(
    p: PersonaAuditInput,
) -> tuple[int, list[str], list[str]]:
    n_supported = sum(1 for t in p.traits if _supported(t))
    n_links = len(p.evidence_links)
    distinct_sources = len({e.source_record_id for e in p.evidence_links})
    base = _clamp(n_supported)
    if distinct_sources >= 4 and base < 5:
        base += 1
    matched = [f"{n_supported} supported traits, {n_links} links, "
               f"{distinct_sources} distinct sources"]
    return base, matched, []


def _exclusion_penalty(
    p: PersonaAuditInput, c: StakeholderCategory,
) -> tuple[int, list[str]]:
    """Subtract up to 10 points if the persona's text contains the
    category's `exclusion_signals`. Each hit is -2; capped at -10."""
    text = _all_text(p)
    keywords = _split_phrases(c.exclusion_signals)
    pat = _build_keyword_regex(keywords)
    hits = _count_hits(text, pat)
    penalty = -min(hits * 2, 10)
    note = []
    if penalty < 0:
        note.append(f"exclusion penalty {penalty} ({hits} exclusion-signal hits)")
    return penalty, note


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_persona_against_category(
    p: PersonaAuditInput,
    c: StakeholderCategory,
    *,
    geography_required: bool,
    weights: dict[str, float] | None = None,
) -> CategoryScoreBreakdown:
    """Compute the full per-axis breakdown.

    Phase 8.2J: when `weights` is supplied, the total score is a
    weighted sum (`sum(sub × weight)` over the 8 axes) plus the
    raw exclusion penalty. When `weights` is None, behavior is
    backwards-compat with Phase 8.2H (uniform 1.0 weight per axis).

    The total is clamped to the integer range [-20, 45] so existing
    Pydantic schemas + classifier thresholds (27 / 36) continue to
    work without change.
    """
    role, role_m, role_x = _score_role_context(p, c)
    pain, pain_m, pain_x = _score_pain_objection(p, c)
    alt, alt_m, alt_x = _score_current_alternative(p, c)
    price, price_m, price_x = _score_price_budget(p, c)
    trust, trust_m, trust_x = _score_trust_trigger(p, c)
    cat, cat_m, cat_x = _score_category_specific(p, c)
    geo, geo_m, geo_x = _score_geography(p, c, geography_required=geography_required)
    src, src_m, src_x = _score_source_strength(p)
    excl, excl_m = _exclusion_penalty(p, c)

    sub_scores = {
        "role_context_match": role,
        "pain_objection_match": pain,
        "current_alternative_match": alt,
        "price_budget_match": price,
        "trust_trigger_match": trust,
        "category_specific_match": cat,
        "geography_match": geo,
        "source_strength": src,
    }
    use_weights = weights if weights is not None else UNIFORM_WEIGHTS
    weighted_total = apply_weights_to_breakdown(sub_scores, use_weights)
    total = int(round(weighted_total)) + excl
    # Clamp to schema bounds.
    total = max(-20, min(45, total))

    matched_signals = (*role_m, *pain_m, *alt_m, *price_m, *trust_m, *cat_m, *geo_m, *src_m, *excl_m)
    missing_signals = (*role_x, *pain_x, *alt_x, *price_x, *trust_x, *cat_x, *geo_x, *src_x)

    return CategoryScoreBreakdown(
        role_context_match=role,
        pain_objection_match=pain,
        current_alternative_match=alt,
        price_budget_match=price,
        trust_trigger_match=trust,
        category_specific_match=cat,
        geography_match=geo,
        source_strength=src,
        exclusion_penalty=excl,
        total_score=total,
        matched_signals=tuple(matched_signals),
        missing_signals=tuple(missing_signals),
    )


def classify_persona_match(total_score: int) -> RelevanceClassification:
    """Map a total score to a Phase-8.2F.7 RelevanceClassification.
    Negative scores clamp to NOT_RELEVANT."""
    if total_score < 0:
        return RelevanceClassification.NOT_RELEVANT
    if total_score > 45:
        total_score = 45
    return classify_total_score(total_score)
