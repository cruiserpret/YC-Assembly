"""Phase 8.2F — source quality classifier.

Heuristic-only. Operates on a single source_record's URL, content, and
metadata, and returns one of four closed-enum classifications. The
classifier is deliberately strict: a generic blog article, agency
landing page, pricing page, or SEO post is classified as
`context_only` and will NOT seed a persona — even if the snippet
incidentally mentions a buyer concern.

The intent is "the article-author is a writer/marketer, not a buyer".
Article surfaces talk ABOUT buyers; only first-person buyer-voice
surfaces are persona seeds.

Heuristic axes:

  1. PERSONA-VOICE markers — first-person pronouns coupled with
     buyer-shaped verbs ("I switched to", "we've been using",
     "I'm frustrated with", "my store").
  2. REVIEW / DISCUSSION markers — multi-paragraph review-shaped
     prose, forum-thread shape.
  3. ARTICLE / MARKETING markers — third-person, "in this article",
     "this guide", "trusted by", "start free trial", "request a demo".
  4. URL-SHAPE markers — `/pricing`, `/features`, `/about`, `/blog/`,
     `/article/`, `/guide/` push toward `context_only`. Forum / review
     URL shapes push toward `strong_persona_signal`.
  5. IDENTITY markers — if the post-redaction content somehow still
     looks identity-heavy, the record is rejected for persona use.

Output is a `SourceClassification` enum value plus a structured
`SourceClassificationReport` carrying the score breakdown so audit
panels can show operators why a record was classified the way it was.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from assembly.pipeline.persona.sensitive_filter import scan_sensitive_attributes


class SourceClassification(str, enum.Enum):
    STRONG_PERSONA_SIGNAL = "strong_persona_signal"
    WEAK_PERSONA_SIGNAL = "weak_persona_signal"
    CONTEXT_ONLY = "context_only"
    REJECT_FOR_SENSITIVE_OR_IDENTITY_RISK = (
        "reject_for_sensitive_or_identity_risk"
    )


@dataclass(frozen=True)
class SourceClassificationReport:
    classification: SourceClassification
    persona_voice_score: int
    article_marketing_score: int
    url_shape_score: int       # positive = persona-ish, negative = marketing-ish
    rationale: tuple[str, ...]


# ---------------------------------------------------------------------------
# Persona-voice detection
# ---------------------------------------------------------------------------


_FIRST_PERSON_RE = re.compile(
    r"\b(?:I|I'?m|I'?ve|my|we'?re|we'?ve|our|us|me|myself|"
    r"as a (?:founder|merchant|operator|seller|owner|user|"
    r"customer|buyer|shopkeeper|store owner|admin|dev))\b",
    re.IGNORECASE,
)
_BUYER_VERB_RE = re.compile(
    r"\b(?:switched|switching|migrated|tried|using|hate|"
    r"love|wish|frustrated|annoyed|burnt out|tired of|"
    r"can'?t|cannot|won'?t|broken|wasted|paid|spent|"
    r"cancel(?:l?ed|ling)?|signed up for|burned by|"
    r"frustrating|disappointing|stuck with|fed up|"
    r"struggling)\b",
    re.IGNORECASE,
)
_REVIEW_TOKEN_RE = re.compile(
    r"\b(?:\d\s?(?:out of\s?)?5(?:\s?stars?)?|rating|review|"
    r"would (?:not\s+)?recommend|pros and cons)\b",
    re.IGNORECASE,
)
_FORUM_DISCUSSION_RE = re.compile(
    r"\b(?:replied|posted|commented|thread|asked|"
    r"yes\s+but|exactly this|this 100%|same here)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Article / marketing detection
# ---------------------------------------------------------------------------


_MARKETING_PHRASES_RE = re.compile(
    r"(?:\bin this (?:article|guide|post|blog)\b|"
    r"\btrusted by\b|"
    r"\bget started (?:today|now|free)\b|"
    r"\bstart (?:your )?free trial\b|"
    r"\brequest a demo\b|"
    r"\bbook a (?:free )?(?:demo|consultation|call|strategy session)\b|"
    r"\bcontact us\b|"
    r"\bsubscribe (?:to (?:our|the))?\s+(?:newsletter|blog)\b|"
    r"\bschedule a call\b|"
    r"\bour (?:platform|product|solution|software)\b|"
    r"\bwe help (?:you|merchants|founders|brands|teams)\b|"
    r"\bour clients\b|"
    r"\bour customers\b|"
    r"\bour (?:agency|team|company)\b|"
    r"\b(?:learn|read) more\b|"
    r"\btable of contents\b|"
    r"\bin this (?:listicle|roundup)\b)",
    re.IGNORECASE,
)
_LISTICLE_HEADER_RE = re.compile(
    r"\b(?:top\s+\d+|best\s+\d+|\d+\s+(?:best|top|reasons|"
    r"tips|ways|tools|strategies|examples|mistakes))\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# URL shape
# ---------------------------------------------------------------------------


_MARKETING_URL_PATH_RE = re.compile(
    r"/(?:pricing|features|about(?:-us)?|services?|solutions?|"
    r"product[s]?|home|index|landing|case-stud(?:y|ies)|"
    r"customers|partners|press|careers?|signup|register|"
    r"contact|demo)(?:/|$|\.html?$)",
    re.IGNORECASE,
)
_BLOG_URL_PATH_RE = re.compile(
    r"/(?:blog|article|articles|posts?|news|insights?|guide|guides|"
    r"resources?|learn|tips|tutorial)/",
    re.IGNORECASE,
)
_PERSONA_URL_PATH_RE = re.compile(
    r"/(?:r/|threads?/|topic/|comments?/|reviews?/|forums?/|"
    r"discussion/|community/)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Identity / sensitive on second pass
# ---------------------------------------------------------------------------


_RESIDUAL_IDENTITY_RE = re.compile(
    r"\b(?:[A-Z][a-z]+\s+[A-Z][a-z]+'s\s+(?:store|shop|brand|company))\b"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_source_record(
    *,
    content: str | None,
    source_url: str | None,
    metadata: dict[str, Any] | None = None,
    user_handle_hash: str | None = None,
) -> SourceClassificationReport:
    """Classify one source_record. Heuristic-only — never calls an LLM."""
    text = (content or "").strip()
    md = metadata or {}
    title = (md.get("title") or "").strip()
    rationale: list[str] = []

    # Sensitive-attribute / residual-identity firewall.
    sensitive_hits = scan_sensitive_attributes(f"{title}\n{text}")
    if sensitive_hits:
        rationale.append(
            "sensitive-attribute hit on second-pass scan: "
            + sorted({h.category.value for h in sensitive_hits})[0]
        )
        return SourceClassificationReport(
            classification=SourceClassification.REJECT_FOR_SENSITIVE_OR_IDENTITY_RISK,
            persona_voice_score=0,
            article_marketing_score=0,
            url_shape_score=0,
            rationale=tuple(rationale),
        )
    if _RESIDUAL_IDENTITY_RE.search(text):
        rationale.append("residual identity-shape match")
        return SourceClassificationReport(
            classification=SourceClassification.REJECT_FOR_SENSITIVE_OR_IDENTITY_RISK,
            persona_voice_score=0,
            article_marketing_score=0,
            url_shape_score=0,
            rationale=tuple(rationale),
        )

    # Persona-voice score.
    first_person_hits = len(_FIRST_PERSON_RE.findall(text))
    buyer_verb_hits = len(_BUYER_VERB_RE.findall(text))
    review_hits = len(_REVIEW_TOKEN_RE.findall(text))
    forum_hits = len(_FORUM_DISCUSSION_RE.findall(text))
    persona_score = (
        min(first_person_hits, 6)
        + min(buyer_verb_hits, 4) * 2
        + min(review_hits, 4) * 2
        + min(forum_hits, 3)
    )
    if first_person_hits:
        rationale.append(f"first_person_markers={first_person_hits}")
    if buyer_verb_hits:
        rationale.append(f"buyer_verb_markers={buyer_verb_hits}")
    if review_hits:
        rationale.append(f"review_markers={review_hits}")
    if forum_hits:
        rationale.append(f"forum_markers={forum_hits}")

    # Article / marketing score.
    marketing_hits = len(_MARKETING_PHRASES_RE.findall(text))
    listicle_hits = len(_LISTICLE_HEADER_RE.findall(f"{title}\n{text}"))
    article_score = min(marketing_hits, 6) * 2 + min(listicle_hits, 3)
    if marketing_hits:
        rationale.append(f"marketing_phrases={marketing_hits}")
    if listicle_hits:
        rationale.append(f"listicle_markers={listicle_hits}")

    # URL shape: positive = persona-ish, negative = marketing-ish.
    url_shape_score = _url_shape_score(source_url, rationale)

    # Tavily metadata signal: results coming from explicit forum/discussion
    # URL paths bias toward persona; an extra small bump.
    if user_handle_hash:
        persona_score += 2
        rationale.append("user_handle_hash present (+2 persona)")

    classification = _decide(
        persona_score=persona_score,
        article_score=article_score,
        url_shape_score=url_shape_score,
        text_length=len(text),
    )
    rationale.append(f"final={classification.value}")

    return SourceClassificationReport(
        classification=classification,
        persona_voice_score=persona_score,
        article_marketing_score=article_score,
        url_shape_score=url_shape_score,
        rationale=tuple(rationale),
    )


def _url_shape_score(url: str | None, rationale: list[str]) -> int:
    if not url:
        return 0
    try:
        path = urlparse(url).path or ""
    except Exception:
        return 0
    score = 0
    if _PERSONA_URL_PATH_RE.search(path):
        score += 4
        rationale.append("persona-shaped URL path (+4)")
    if _BLOG_URL_PATH_RE.search(path):
        score -= 3
        rationale.append("blog-shaped URL path (-3)")
    if _MARKETING_URL_PATH_RE.search(path):
        score -= 4
        rationale.append("marketing-shaped URL path (-4)")
    return score


def _decide(
    *,
    persona_score: int,
    article_score: int,
    url_shape_score: int,
    text_length: int,
) -> SourceClassification:
    # Very short snippets are almost always non-persona. We refuse to
    # call them strong_persona_signal even when first-person markers hit
    # — too little to extract ≥3 traits from.
    if text_length < 120:
        return SourceClassification.CONTEXT_ONLY

    net = persona_score + url_shape_score - article_score

    # Strong-marketing signal trumps a few stray first-person hits.
    if article_score >= 6 and persona_score < article_score:
        return SourceClassification.CONTEXT_ONLY
    if url_shape_score <= -3 and persona_score < 6:
        return SourceClassification.CONTEXT_ONLY

    if net >= 6:
        return SourceClassification.STRONG_PERSONA_SIGNAL
    if net >= 3:
        return SourceClassification.WEAK_PERSONA_SIGNAL
    return SourceClassification.CONTEXT_ONLY
