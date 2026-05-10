"""Phase 8.4A.3 — market-entry anchor detector.

Plan-driven: pulls anchor terms directly from the dynamically-
generated stakeholder categories of a `TargetSocietyPlan`. Has NO
product-family-specific knowledge (no hardcoded "Red Bull" /
"sunscreen" / "Shopify" terms — they come from the brief's
competitors / substitutes / use-cases via the dynamic planner).

Anchor types (mirroring the operator spec):
  * competitor_anchor       — persona text mentions a brand named in
                              `brief.competitors[]`
  * substitute_anchor       — persona text mentions a substitute
                              parsed from `brief.extra_context` /
                              `brief.product_description`
  * use_case_anchor         — persona text mentions a target user
                              role parsed from `brief.intended_user_or_buyer`
  * category_objection_anchor — persona text matches one of the
                              universal objection patterns
  * buyer_type_anchor       — persona text matches one of the
                              universal buyer-type patterns

A persona has `has_anchor=True` if ANY anchor type fires.

This module is deterministic, never calls an LLM, never calls the
network, never writes to the DB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from assembly.pipeline.persona_relevance.auditor import PersonaAuditInput
from assembly.pipeline.target_society.schemas import (
    StakeholderCategory,
    TargetSocietyPlan,
)


# ---------------------------------------------------------------------------
# Anchor types — closed enum (string literals, not Enum, for simpler
# JSON-serialization in the replay audit JSON).
# ---------------------------------------------------------------------------


ANCHOR_COMPETITOR: Final[str] = "competitor_anchor"
ANCHOR_SUBSTITUTE: Final[str] = "substitute_anchor"
ANCHOR_USE_CASE: Final[str] = "use_case_anchor"
ANCHOR_CATEGORY_OBJECTION: Final[str] = "category_objection_anchor"
ANCHOR_BUYER_TYPE: Final[str] = "buyer_type_anchor"

ALL_ANCHOR_TYPES: Final[tuple[str, ...]] = (
    ANCHOR_COMPETITOR,
    ANCHOR_SUBSTITUTE,
    ANCHOR_USE_CASE,
    ANCHOR_CATEGORY_OBJECTION,
    ANCHOR_BUYER_TYPE,
)


# Map each Phase 8.4A.2 dynamic-planner category-key prefix to its
# anchor type. The detector reads the plan's stakeholder_categories
# and groups inclusion_signals by anchor type via this mapping.
_PREFIX_TO_ANCHOR_TYPE: Final[dict[str, str]] = {
    "competitor_user_": ANCHOR_COMPETITOR,
    "substitute_user_": ANCHOR_SUBSTITUTE,
    "use_case_": ANCHOR_USE_CASE,
    "objection_": ANCHOR_CATEGORY_OBJECTION,
    "buyer_type_": ANCHOR_BUYER_TYPE,
}


# ---------------------------------------------------------------------------
# AnchorReport — the detector's structured output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorReport:
    has_anchor: bool
    anchor_types: tuple[str, ...]
    matched_anchor_terms: tuple[str, ...]
    anchor_evidence_excerpts: tuple[str, ...]
    explanation: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Use word-boundary regex so "monster" matches "Monster Energy" but NOT
# "monsterously" (defensive).
_WORD_BOUNDARY = r"(?<![A-Za-z0-9])"
_WORD_BOUNDARY_END = r"(?![A-Za-z0-9])"


def _build_term_regex(terms: list[str]) -> re.Pattern[str] | None:
    """Build a case-insensitive word-boundary alternation. Returns
    None when the list is empty (so the caller can short-circuit)."""
    cleaned = [
        re.escape(t.lower())
        for t in terms
        if t and len(t.strip()) >= 2
    ]
    if not cleaned:
        return None
    return re.compile(
        _WORD_BOUNDARY + r"(?:" + "|".join(cleaned) + r")" + _WORD_BOUNDARY_END,
        re.IGNORECASE,
    )


def _persona_text_blob(persona: PersonaAuditInput) -> str:
    """Concatenate every text leaf the persona owns: trait values,
    trait rationales, and evidence-link excerpts."""
    parts: list[str] = []
    for t in persona.traits:
        if t.value:
            parts.append(t.value)
        if t.rationale:
            parts.append(t.rationale)
    for el in persona.evidence_links:
        if el.excerpt:
            parts.append(el.excerpt)
    return "\n".join(parts)


def _extract_anchor_term_groups(
    plan: TargetSocietyPlan,
) -> dict[str, list[str]]:
    """Group the plan's stakeholder-category inclusion_signals by
    anchor type. Returns a dict[anchor_type → list[term]]."""
    groups: dict[str, list[str]] = {a: [] for a in ALL_ANCHOR_TYPES}
    for cat in plan.stakeholder_categories:
        anchor_type = _classify_category_to_anchor(cat)
        if anchor_type is None:
            continue
        for signal in cat.inclusion_signals:
            if signal and signal.strip() and signal not in groups[anchor_type]:
                groups[anchor_type].append(signal)
    # Dedup (preserve order)
    for k in groups:
        seen: set[str] = set()
        unique: list[str] = []
        for term in groups[k]:
            low = term.lower()
            if low not in seen:
                seen.add(low)
                unique.append(term)
        groups[k] = unique
    return groups


def _classify_category_to_anchor(c: StakeholderCategory) -> str | None:
    """Map a category by `category_key` prefix to its anchor type.
    Categories outside the dynamic-planner naming scheme (e.g. classic
    CPG categories) are not anchored and return None."""
    for prefix, anchor in _PREFIX_TO_ANCHOR_TYPE.items():
        if c.category_key.startswith(prefix):
            return anchor
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_market_entry_anchors(
    persona: PersonaAuditInput,
    plan: TargetSocietyPlan,
) -> AnchorReport:
    """Inspect a persona's traits + evidence excerpts. Return whether
    it has at least one evidence-backed market-entry anchor.

    The detector is plan-driven: anchor terms come from the dynamic-
    planner-generated stakeholder categories. For an Amboras-style
    classic plan (no `competitor_user_*` / `use_case_*` categories),
    no anchors fire — the gate would not apply (it's a market-entry-
    mode-only mechanism).

    Returns an `AnchorReport` with:
      * `has_anchor` — True if any anchor type matched
      * `anchor_types` — tuple of matched anchor types (closed enum)
      * `matched_anchor_terms` — tuple of unique terms that matched
      * `anchor_evidence_excerpts` — up to 3 evidence excerpts that
        contain at least one matched anchor term
      * `explanation` — human-readable summary
    """
    blob = _persona_text_blob(persona)
    if not blob:
        return AnchorReport(
            has_anchor=False,
            anchor_types=tuple(),
            matched_anchor_terms=tuple(),
            anchor_evidence_excerpts=tuple(),
            explanation="persona has no trait or excerpt text",
        )

    term_groups = _extract_anchor_term_groups(plan)
    matched_types: list[str] = []
    matched_terms: list[str] = []
    for anchor_type in ALL_ANCHOR_TYPES:
        terms = term_groups.get(anchor_type, [])
        pat = _build_term_regex(terms)
        if pat is None:
            continue
        hits = pat.findall(blob)
        if not hits:
            continue
        matched_types.append(anchor_type)
        for h in hits:
            if h.lower() not in (m.lower() for m in matched_terms):
                matched_terms.append(h)

    # Pull up to 3 evidence excerpts that contain at least one matched
    # term — these are the auditable anchor witnesses.
    excerpts: list[str] = []
    if matched_terms:
        all_terms_pat = _build_term_regex(matched_terms)
        if all_terms_pat is not None:
            for el in persona.evidence_links:
                if not el.excerpt:
                    continue
                if all_terms_pat.search(el.excerpt):
                    excerpts.append(el.excerpt[:300])
                    if len(excerpts) >= 3:
                        break

    has_anchor = bool(matched_types)
    if has_anchor:
        explanation = (
            f"persona text matched {len(matched_types)} anchor type(s): "
            f"{matched_types}; matched terms: "
            f"{matched_terms[:8]}"
        )
    else:
        explanation = (
            "persona text contains NO competitor / substitute / "
            "use-case / category-objection / buyer-type anchor "
            "from the brief's plan"
        )
    return AnchorReport(
        has_anchor=has_anchor,
        anchor_types=tuple(matched_types),
        matched_anchor_terms=tuple(matched_terms),
        anchor_evidence_excerpts=tuple(excerpts),
        explanation=explanation,
    )


__all__ = [
    "ALL_ANCHOR_TYPES",
    "ANCHOR_BUYER_TYPE",
    "ANCHOR_CATEGORY_OBJECTION",
    "ANCHOR_COMPETITOR",
    "ANCHOR_SUBSTITUTE",
    "ANCHOR_USE_CASE",
    "AnchorReport",
    "detect_market_entry_anchors",
]
