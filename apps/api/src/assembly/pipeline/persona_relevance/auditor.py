"""Phase 8.2F.7 — deterministic per-persona relevance scorer + aggregator.

`score_persona(...)` and `audit_personas(...)` are PURE — they take
ORM-shaped data structures, never call out to a database, never write
anything, never mutate inputs.

The aggregator owns the viewpoint-diversity computation, since it
requires comparing personas to each other.

Design notes:

  - Scoring functions never read source URLs; only persona trait values
    + bound source excerpts (which the redaction pipeline already
    sanitized). This guarantees identity isolation in the audit path.

  - All scores are clamped to [0, SCORE_MAX_PER_FIELD]. The total is
    just the sum.

  - "unknown" / "missing" persona traits cannot inflate any sub-score;
    they contribute zero to the keyword counters.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from assembly.pipeline.persona_relevance.rubric import (
    ALTERNATIVE_KEYWORDS_RE,
    PAIN_KEYWORDS_RE,
    PRICE_KEYWORDS_RE,
    ROLE_KEYWORDS_RE,
    SCORE_FIELDS,
    SCORE_MAX_PER_FIELD,
    STAKEHOLDER_CATEGORIES,
    STAKEHOLDER_REQUIREMENTS,
    StakeholderCategory,
    TRUST_OBJECTION_KEYWORDS_RE,
    RelevanceClassification,
    classify_total_score,
)


# ---------------------------------------------------------------------------
# Audit input — caller provides one of these per persona.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraitView:
    """Compact persona-trait view the auditor needs."""
    field_name: str
    support_level: str
    value: str | None
    confidence: float
    source_ids: tuple[UUID, ...] = ()
    rationale: str | None = None


@dataclass(frozen=True)
class EvidenceLinkView:
    """Compact persona_evidence_link view + linked source content."""
    persona_id: UUID
    source_record_id: UUID
    contribution_kind: str
    contribution_field: str
    excerpt: str
    source_likely_human_signal: bool | None = None


@dataclass(frozen=True)
class PersonaAuditInput:
    persona_id: UUID
    display_name: str
    traits: tuple[TraitView, ...]
    evidence_links: tuple[EvidenceLinkView, ...]


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonaRelevanceScore:
    persona_id: UUID
    display_name: str
    role_context_score: int
    pain_point_score: int
    current_alternative_score: int
    price_budget_score: int
    trust_objection_score: int
    source_strength_score: int
    human_signal_score: int
    viewpoint_diversity_score: int
    simulation_usefulness_score: int
    total_score: int
    classification: RelevanceClassification
    matched_stakeholder_categories: tuple[StakeholderCategory, ...]
    rationale: tuple[str, ...] = ()
    matched_keyword_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AggregateAuditResult:
    personas_audited: int
    classification_counts: dict[RelevanceClassification, int]
    average_scores: dict[str, float]
    per_persona: tuple[PersonaRelevanceScore, ...]
    matched_categories: dict[StakeholderCategory, int]
    missing_categories: tuple[StakeholderCategory, ...]
    duplicate_fingerprints: dict[str, int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(score: int, lo: int = 0, hi: int = SCORE_MAX_PER_FIELD) -> int:
    return max(lo, min(hi, score))


def _supported(t: TraitView) -> bool:
    return (
        t.support_level in ("direct", "inferred")
        and t.value is not None
        and t.value.strip() != ""
    )


def _all_text_blobs(p: PersonaAuditInput) -> str:
    """One concatenated string of every supported trait value + every
    evidence excerpt. Lower-cased once for keyword matches."""
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


def _trait_text_for_field(p: PersonaAuditInput, field_name: str) -> str:
    parts: list[str] = []
    for t in p.traits:
        if t.field_name == field_name and _supported(t):
            parts.append(t.value or "")
    return "\n".join(parts).lower()


def _count_pattern_hits(text: str, pat) -> int:
    if not text:
        return 0
    return len(pat.findall(text))


# ---------------------------------------------------------------------------
# Per-axis scoring
# ---------------------------------------------------------------------------


def _score_role_context(p: PersonaAuditInput) -> tuple[int, int]:
    text = _trait_text_for_field(p, "role_or_context")
    if not text:
        # Fall back to evidence excerpts only; weaker signal.
        excerpts = "\n".join(e.excerpt or "" for e in p.evidence_links).lower()
        hits = _count_pattern_hits(excerpts, ROLE_KEYWORDS_RE)
        return _clamp(min(hits, 3)), hits
    hits = _count_pattern_hits(text, ROLE_KEYWORDS_RE)
    if hits == 0:
        return 0, 0
    if hits == 1:
        return 3, hits
    if hits == 2:
        return 4, hits
    return 5, hits


def _score_pain_points(p: PersonaAuditInput) -> tuple[int, int]:
    text = _all_text_blobs(p)
    hits = _count_pattern_hits(text, PAIN_KEYWORDS_RE)
    return _clamp(min(hits, 5)), hits


def _score_current_alternatives(p: PersonaAuditInput) -> tuple[int, int]:
    field_text = _trait_text_for_field(p, "current_alternatives")
    excerpt_text = "\n".join(e.excerpt or "" for e in p.evidence_links).lower()
    hits_field = _count_pattern_hits(field_text, ALTERNATIVE_KEYWORDS_RE)
    hits_excerpt = _count_pattern_hits(excerpt_text, ALTERNATIVE_KEYWORDS_RE)
    # Field hits weigh double — they're persona-bound, not just incidental
    # mentions in the source.
    composite = hits_field * 2 + hits_excerpt
    return _clamp(min(composite, 5)), composite


def _score_price_budget(p: PersonaAuditInput) -> tuple[int, int]:
    parts = [
        _trait_text_for_field(p, "price_sensitivity"),
        _trait_text_for_field(p, "buying_constraints"),
    ]
    text = "\n".join(parts)
    excerpt_text = "\n".join(e.excerpt or "" for e in p.evidence_links).lower()
    hits = (
        _count_pattern_hits(text, PRICE_KEYWORDS_RE) * 2
        + _count_pattern_hits(excerpt_text, PRICE_KEYWORDS_RE)
    )
    # Bonus: source-backed price_sensitivity trait contributes a floor.
    has_price_sensitivity_trait = any(
        t.field_name == "price_sensitivity" and _supported(t)
        for t in p.traits
    )
    score = min(hits, 5)
    if has_price_sensitivity_trait and score < 3:
        score = 3
    return _clamp(score), hits


def _score_trust_objection(p: PersonaAuditInput) -> tuple[int, int]:
    parts = [
        _trait_text_for_field(p, "trust_triggers"),
        _trait_text_for_field(p, "objection_patterns"),
    ]
    text = "\n".join(parts)
    excerpt_text = "\n".join(e.excerpt or "" for e in p.evidence_links).lower()
    hits = (
        _count_pattern_hits(text, TRUST_OBJECTION_KEYWORDS_RE) * 2
        + _count_pattern_hits(excerpt_text, TRUST_OBJECTION_KEYWORDS_RE)
    )
    has_trust_trait = any(
        t.field_name in ("trust_triggers", "objection_patterns") and _supported(t)
        for t in p.traits
    )
    score = min(hits, 5)
    if has_trust_trait and score < 2:
        score = 2
    return _clamp(score), hits


def _score_source_strength(p: PersonaAuditInput) -> tuple[int, int]:
    n_supported = sum(1 for t in p.traits if _supported(t))
    n_links = len(p.evidence_links)
    # 0 supported → 0; 1 → 1; 2 → 2; 3 → 3; 4 → 4; 5+ → 5.
    # Then scale up by evidence-link breadth (≥ 4 unique sources = +1).
    base = _clamp(n_supported)
    distinct_sources = len({e.source_record_id for e in p.evidence_links})
    if distinct_sources >= 4 and base < 5:
        base += 1
    composite = n_supported * 10 + n_links
    return _clamp(base), composite


def _score_human_signal(p: PersonaAuditInput) -> tuple[int, int]:
    if not p.evidence_links:
        return 0, 0
    flags = [
        bool(e.source_likely_human_signal)
        for e in p.evidence_links
        if e.source_likely_human_signal is not None
    ]
    if not flags:
        # No metadata → 1 (uncertain but not zero — we have at least one evidence_link)
        return 1, 0
    pct = sum(flags) / len(flags)
    if pct >= 0.95:
        return 5, int(pct * 100)
    if pct >= 0.75:
        return 4, int(pct * 100)
    if pct >= 0.5:
        return 3, int(pct * 100)
    if pct >= 0.25:
        return 2, int(pct * 100)
    if pct > 0:
        return 1, int(pct * 100)
    return 0, 0


def _persona_fingerprint(p: PersonaAuditInput) -> str:
    """Coarse fingerprint used for redundancy/diversity detection.

    We pick the strongest source-backed value of each of three axes:
    role, pains, alternatives. Personas with the same fingerprint are
    "viewpoint-redundant" with each other.
    """
    def _first_supported(field_name: str) -> str:
        for t in p.traits:
            if t.field_name == field_name and _supported(t):
                return _normalize_for_fingerprint(t.value or "")
        return ""

    role = _first_supported("role_or_context")[:60]
    pains = _first_supported("objection_patterns")[:60]
    alt = _first_supported("current_alternatives")[:60]
    return "|".join((role, pains, alt))


_NORMALIZE_RE = None


def _normalize_for_fingerprint(text: str) -> str:
    import re
    global _NORMALIZE_RE
    if _NORMALIZE_RE is None:
        _NORMALIZE_RE = re.compile(r"[^a-z0-9 ]+")
    return _NORMALIZE_RE.sub("", text.lower()).strip()


# ---------------------------------------------------------------------------
# Composite simulation usefulness
# ---------------------------------------------------------------------------


def _score_simulation_usefulness(
    p: PersonaAuditInput,
    *,
    role_score: int,
    pain_score: int,
    alt_score: int,
    source_strength: int,
) -> tuple[int, int]:
    """A persona is "simulation-useful" iff it carries a real role +
    real pain or alternative + decent source strength. The score
    composes the four sub-scores.

    Normalised to 0..5: each input axis contributes up to ~1.25, then
    we cap at 5.
    """
    composite = (
        (role_score / 5) + (pain_score / 5)
        + (alt_score / 5) + (source_strength / 5)
    ) * (5 / 4)
    score = int(round(composite))
    return _clamp(score), int(composite * 100)


# ---------------------------------------------------------------------------
# Stakeholder category matching
# ---------------------------------------------------------------------------


def _match_stakeholder_categories(
    p: PersonaAuditInput,
) -> tuple[StakeholderCategory, ...]:
    """Return every stakeholder category whose role + pain keyword
    requirements both match somewhere in the persona's text blobs.
    """
    text = _all_text_blobs(p)
    matched: list[StakeholderCategory] = []
    for cat in STAKEHOLDER_CATEGORIES:
        spec = STAKEHOLDER_REQUIREMENTS[cat]
        role_ok = any(k.lower() in text for k in spec["role_keywords"])
        pain_ok = any(k.lower() in text for k in spec["pain_keywords"])
        if role_ok and pain_ok:
            matched.append(cat)
    return tuple(matched)


# ---------------------------------------------------------------------------
# Public per-persona scorer
# ---------------------------------------------------------------------------


def score_persona(
    p: PersonaAuditInput,
    *,
    fingerprint_freq: dict[str, int] | None = None,
) -> PersonaRelevanceScore:
    """Score one persona. The aggregator computes
    `fingerprint_freq` once across the whole batch so the
    viewpoint-diversity score reflects this persona's uniqueness.
    """
    fingerprint_freq = fingerprint_freq or {}
    role_score, role_hits = _score_role_context(p)
    pain_score, pain_hits = _score_pain_points(p)
    alt_score, alt_hits = _score_current_alternatives(p)
    price_score, price_hits = _score_price_budget(p)
    trust_score, trust_hits = _score_trust_objection(p)
    source_strength, source_strength_meta = _score_source_strength(p)
    human_signal_score, human_pct = _score_human_signal(p)
    fingerprint = _persona_fingerprint(p)
    freq = fingerprint_freq.get(fingerprint, 1)
    if freq == 1:
        diversity_score = 5
    elif freq == 2:
        diversity_score = 4
    elif freq == 3:
        diversity_score = 3
    elif freq == 4:
        diversity_score = 2
    else:
        diversity_score = 1

    sim_score, sim_meta = _score_simulation_usefulness(
        p,
        role_score=role_score,
        pain_score=pain_score,
        alt_score=alt_score,
        source_strength=source_strength,
    )

    total = (
        role_score + pain_score + alt_score + price_score + trust_score
        + source_strength + human_signal_score + diversity_score + sim_score
    )

    matched = _match_stakeholder_categories(p)

    rationale = (
        f"role={role_score}({role_hits} hits)",
        f"pains={pain_score}({pain_hits})",
        f"alts={alt_score}({alt_hits})",
        f"price={price_score}({price_hits})",
        f"trust={trust_score}({trust_hits})",
        f"source_strength={source_strength}",
        f"human_signal={human_signal_score}",
        f"diversity={diversity_score} (fp_freq={freq})",
        f"sim_useful={sim_score}",
        f"matched_categories={[c.value for c in matched]}",
    )

    return PersonaRelevanceScore(
        persona_id=p.persona_id,
        display_name=p.display_name,
        role_context_score=role_score,
        pain_point_score=pain_score,
        current_alternative_score=alt_score,
        price_budget_score=price_score,
        trust_objection_score=trust_score,
        source_strength_score=source_strength,
        human_signal_score=human_signal_score,
        viewpoint_diversity_score=diversity_score,
        simulation_usefulness_score=sim_score,
        total_score=total,
        classification=classify_total_score(total),
        matched_stakeholder_categories=matched,
        rationale=rationale,
        matched_keyword_counts={
            "role": role_hits,
            "pains": pain_hits,
            "alternatives": alt_hits,
            "price": price_hits,
            "trust": trust_hits,
            "source_strength_meta": source_strength_meta,
            "human_signal_pct": human_pct,
            "fingerprint_freq": freq,
        },
    )


# ---------------------------------------------------------------------------
# Aggregate audit
# ---------------------------------------------------------------------------


def audit_personas(
    personas: Iterable[PersonaAuditInput],
) -> AggregateAuditResult:
    """Score every persona and return aggregate stats."""
    items: list[PersonaAuditInput] = list(personas)

    # Pass 1 — fingerprint frequencies.
    fingerprints: dict[str, int] = {}
    for p in items:
        fp = _persona_fingerprint(p)
        fingerprints[fp] = fingerprints.get(fp, 0) + 1

    # Pass 2 — per-persona scoring.
    scores: list[PersonaRelevanceScore] = []
    for p in items:
        scores.append(score_persona(p, fingerprint_freq=fingerprints))

    # Aggregate stats.
    classification_counts = {
        c: sum(1 for s in scores if s.classification == c)
        for c in RelevanceClassification
    }
    avg_scores: dict[str, float] = {}
    for f in SCORE_FIELDS:
        if scores:
            avg_scores[f] = round(
                sum(getattr(s, f) for s in scores) / len(scores), 2
            )
        else:
            avg_scores[f] = 0.0

    matched_counts: dict[StakeholderCategory, int] = {
        c: 0 for c in STAKEHOLDER_CATEGORIES
    }
    for s in scores:
        for cat in s.matched_stakeholder_categories:
            matched_counts[cat] += 1
    missing = tuple(c for c in STAKEHOLDER_CATEGORIES if matched_counts[c] == 0)

    duplicate_fingerprints = {
        fp: n for fp, n in fingerprints.items() if n >= 2
    }

    return AggregateAuditResult(
        personas_audited=len(items),
        classification_counts=classification_counts,
        average_scores=avg_scores,
        per_persona=tuple(scores),
        matched_categories=matched_counts,
        missing_categories=missing,
        duplicate_fingerprints=duplicate_fingerprints,
    )
