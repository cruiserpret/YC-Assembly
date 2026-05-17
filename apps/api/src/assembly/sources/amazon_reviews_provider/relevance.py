"""Phase 11C.6 — product-shape relevance scorer for Amazon signals.

Pure-function, deterministic, no LLM, no I/O. The scorer turns a
brief + a RetrievedSignal into a relevance score in [0, 1] plus a
detail breakdown the audit can record. The persona-injection
pipeline uses this to drop noise (e.g. gaming snippets surfacing
on a browser-extension brief in the Software category) before the
per-bucket balancer picks the final 8–12 snippets.

The scorer is designed for OBSERVABILITY: every component is broken
out so the operator can audit why a given signal was kept or
dropped. Score weights are tunable constants at the top of the
file; any future change should re-run the Phase-11C.6 A/B/C/D
validation harness to confirm filter quality didn't regress.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from assembly.sources.amazon_reviews_provider.retrieval import (
        ProductBriefShape,
        RetrievedSignal,
    )


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


# Signal-type usefulness weights. Negative-leaning buyer concerns
# score highest because they're the most informative for persona
# objection generation. Generic praise is downranked because it
# rarely carries product-specific information.
_SIGNAL_TYPE_WEIGHTS: dict[str, float] = {
    "objection": 1.00,
    "trust": 1.00,
    "setup": 1.00,
    "support": 1.00,
    "durability": 0.95,
    "return_reason": 0.95,
    "price": 0.95,
    "safety": 0.90,
    "switch_reason": 0.85,
    "use_case": 0.70,
    "proof_need": 0.60,
    "praise": 0.40,
}

# Component weights — sum to 1.0 for the "structural" score before
# bonus / penalty adjustments.
_W_TITLE_OVERLAP = 0.35
_W_SNIPPET_OVERLAP = 0.30
_W_SIGNAL_TYPE = 0.25
_W_COMPETITOR = 0.10
# Bonuses (additive, capped at 1.0 final).
_BONUS_VERIFIED = 0.05
_BONUS_HELPFUL = 0.05
# Penalties (subtractive).
_PENALTY_GENERIC_DISAPPOINTMENT_NO_REASON = 0.30
_PENALTY_GENERIC_PRAISE_NO_REASON = 0.25
_PENALTY_VERY_SHORT_SNIPPET = 0.15
# Off-topic guard — caps the score for any snippet that has
# essentially zero word connection to the brief.
_PENALTY_OFF_TOPIC = 0.20


# Stopwords filtered before tokenization. Deliberately small — we
# want product-specific words (browser, extension, wellness, stress,
# sensor) to dominate the overlap signal.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "of", "in", "on",
        "at", "to", "with", "for", "from", "by", "as", "is", "are",
        "was", "were", "be", "been", "being", "this", "that",
        "these", "those", "it", "its", "i", "we", "you", "they",
        "my", "our", "your", "their", "have", "has", "had", "do",
        "does", "did", "not", "no", "yes", "if", "then", "than",
        "so", "very", "also", "just", "can", "will", "would",
        "should", "could", "may", "might", "any", "all", "some",
        "more", "less", "most", "least", "much", "many", "such",
        "only", "own", "same", "other", "another", "each",
        "every", "few", "lot", "lots",
    },
)


# How many tokens are "specific" enough to count for overlap.
# Single-letter or numeric-only tokens get filtered.
_MIN_TOKEN_LEN = 3


# Generic-praise / generic-disappointment patterns (rough, in
# addition to the distiller's own themes).
_GENERIC_PRAISE_RE = re.compile(
    r"^(i\s+)?(love|like)\s+(it|this|them)[\s!.]*$",
    re.IGNORECASE,
)
_GENERIC_DISAPPOINT_RE = re.compile(
    r"^(i'?m\s+)?(disappointed|disappointing|bad|terrible)[\s!.]*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalRelevanceScore:
    """Per-signal score breakdown — exposes WHY a signal scored what
    it did, so the audit (and tests) can be specific about filter
    decisions."""

    total: float                  # final score, clamped [0, 1]
    title_overlap: float          # Jaccard against brief tokens
    snippet_overlap: float        # Jaccard against brief tokens
    signal_type_weight: float     # from _SIGNAL_TYPE_WEIGHTS
    competitor_match: bool
    verified_bonus: float
    helpful_bonus: float
    penalty: float
    drop_reason: str | None       # populated only when total < threshold


# ---------------------------------------------------------------------------
# Tokenization + overlap math
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    """Lowercase, drop punctuation, keep alpha-numeric tokens ≥ 3
    chars and not in the stopword list."""
    if not text:
        return set()
    raw = re.findall(r"[a-zA-Z][a-zA-Z]+", text.lower())
    return {
        t for t in raw
        if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / max(len(union), 1)


def _brief_tokens(brief: "ProductBriefShape") -> set[str]:
    """Combined keyword set extracted from the brief's text fields.
    Cached at the call site is fine — each scoring pass calls this
    once and re-uses for every signal."""
    blob = " ".join([
        brief.product_name or "",
        brief.description or "",
        brief.category_hint or "",
    ])
    return _tokens(blob)


def _has_competitor_match(
    signal: "RetrievedSignal",
    competitor_names_lower: frozenset[str],
) -> bool:
    """True when the signal's brand or competitor_mention contains
    any of the brief's competitor names (case-insensitive substring
    match — `Apollo` matches `Apollo Neuro`)."""
    if not competitor_names_lower:
        return False
    for field in (signal.brand, signal.competitor_mention):
        if not field:
            continue
        f_lower = field.lower()
        for c in competitor_names_lower:
            if c in f_lower:
                return True
    return False


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------


def score_signal_for_brief(
    signal: "RetrievedSignal",
    *,
    brief: "ProductBriefShape",
    brief_token_set: set[str] | None = None,
) -> SignalRelevanceScore:
    """Deterministic relevance score for one signal vs. one brief.

    Pass `brief_token_set` when scoring many signals against the
    same brief — saves the tokenization cost N-1 times.
    """
    brief_tokens = (
        brief_token_set
        if brief_token_set is not None
        else _brief_tokens(brief)
    )
    title_tokens = _tokens(signal.product_title or "")
    snippet_tokens = _tokens(signal.short_snippet or "")

    title_overlap = _jaccard(brief_tokens, title_tokens)
    snippet_overlap = _jaccard(brief_tokens, snippet_tokens)
    type_weight = _SIGNAL_TYPE_WEIGHTS.get(signal.signal_type, 0.50)

    competitor_names_lower = frozenset(
        (c or "").strip().lower()
        for c in brief.competitors
        if c and c.strip()
    )
    competitor = _has_competitor_match(signal, competitor_names_lower)
    competitor_score = 1.0 if competitor else 0.0

    # Bonuses.
    verified_bonus = (
        _BONUS_VERIFIED if signal.verified_purchase else 0.0
    )
    helpful_bonus = (
        _BONUS_HELPFUL
        if (signal.helpful_votes or 0) >= 5 else 0.0
    )

    # Penalties.
    penalty = 0.0
    snippet = (signal.short_snippet or "").strip()
    if len(snippet) < 30:
        penalty += _PENALTY_VERY_SHORT_SNIPPET
    # Off-topic guard: a signal that shares ALMOST nothing with the
    # brief (≤ 5% title overlap AND ≤ 10% snippet overlap) is by
    # definition not about this product, regardless of how
    # high-value its signal_type would otherwise be. Catches
    # category-level noise like sponge / bottle / gaming snippets
    # surfacing for unrelated briefs.
    #
    # EXCEPTION: a competitor match indicates the snippet is about
    # the brief's named competitor — that's relevant even if word
    # overlap is low (different brand uses different product
    # vocabulary). Operator spec explicitly asks the filter to
    # "prefer competitor matches", so we skip the off-topic
    # penalty in that case.
    if (
        title_overlap <= 0.05
        and snippet_overlap <= 0.10
        and not competitor
    ):
        penalty += _PENALTY_OFF_TOPIC
    if (
        signal.theme == "generic_disappointment"
        and snippet_overlap <= 0.10
    ):
        penalty += _PENALTY_GENERIC_DISAPPOINTMENT_NO_REASON
    if signal.theme == "general_praise" and snippet_overlap <= 0.10:
        penalty += _PENALTY_GENERIC_PRAISE_NO_REASON
    if _GENERIC_PRAISE_RE.match(snippet):
        penalty += _PENALTY_GENERIC_PRAISE_NO_REASON
    if _GENERIC_DISAPPOINT_RE.match(snippet):
        penalty += _PENALTY_GENERIC_DISAPPOINTMENT_NO_REASON

    structural = (
        _W_TITLE_OVERLAP * title_overlap
        + _W_SNIPPET_OVERLAP * snippet_overlap
        + _W_SIGNAL_TYPE * type_weight
        + _W_COMPETITOR * competitor_score
    )
    total = structural + verified_bonus + helpful_bonus - penalty
    # Clamp to [0, 1].
    total = max(0.0, min(1.0, total))

    return SignalRelevanceScore(
        total=round(total, 4),
        title_overlap=round(title_overlap, 4),
        snippet_overlap=round(snippet_overlap, 4),
        signal_type_weight=type_weight,
        competitor_match=competitor,
        verified_bonus=verified_bonus,
        helpful_bonus=helpful_bonus,
        penalty=round(penalty, 4),
        drop_reason=None,  # filled in by the filter when applicable
    )


# ---------------------------------------------------------------------------
# Filter helper
# ---------------------------------------------------------------------------


def _drop_reason(score: SignalRelevanceScore, threshold: float) -> str:
    """Compact reason string for audit. Prioritized in the order an
    operator would likely investigate."""
    if score.total >= threshold:
        return ""
    if score.penalty >= 0.25:
        return "high_noise_penalty"
    if score.snippet_overlap < 0.05 and score.title_overlap < 0.05:
        return "no_keyword_overlap_with_brief"
    if score.signal_type_weight < 0.50:
        return "low_signal_type_weight"
    return "below_threshold"


def filter_signals_by_relevance(
    signals: "list[RetrievedSignal]",
    *,
    brief: "ProductBriefShape",
    min_score: float,
) -> tuple[
    "list[RetrievedSignal]",
    "list[tuple[RetrievedSignal, SignalRelevanceScore]]",
]:
    """Score every signal and split into (kept, rejected).

    Both lists preserve input order. Each rejected entry pairs the
    signal with its score so the audit dict can show the operator
    why specific snippets were dropped.

    Use `min_score=0.0` to disable filtering — every signal scores
    and survives, mimicking the Phase-11C.5 behavior.
    """
    brief_token_set = _brief_tokens(brief)
    kept: list = []
    rejected: list = []
    for s in signals:
        score = score_signal_for_brief(
            s, brief=brief, brief_token_set=brief_token_set,
        )
        if score.total >= min_score:
            kept.append(s)
        else:
            # Record the drop reason on the score.
            reason = _drop_reason(score, min_score)
            score_with_reason = SignalRelevanceScore(
                total=score.total,
                title_overlap=score.title_overlap,
                snippet_overlap=score.snippet_overlap,
                signal_type_weight=score.signal_type_weight,
                competitor_match=score.competitor_match,
                verified_bonus=score.verified_bonus,
                helpful_bonus=score.helpful_bonus,
                penalty=score.penalty,
                drop_reason=reason,
            )
            rejected.append((s, score_with_reason))
    return kept, rejected


__all__ = [
    "SignalRelevanceScore",
    "filter_signals_by_relevance",
    "score_signal_for_brief",
]
