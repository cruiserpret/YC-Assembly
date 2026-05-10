"""Phase 10B.6 — Explicit Negative-Feature Fact Lock.

Generic, product-agnostic extractor for explicit "does not have / does
not use / does not record / is not a / no X" statements in any
founder brief. Phase 10B.4 hand-curated negation patterns for a few
common sensing facts (camera, video, livestream, face recognition);
10B.6 generalizes that so the lock catches ANY feature the founder
explicitly denies.

The extractor:
  * walks the product description + optional context sentence-by-
    sentence,
  * finds explicit negation patterns,
  * normalizes the noun phrase ("a camera" → "camera", "GPS
    receiver" → "gps receiver"),
  * stores each as a `ForbiddenFeature` carrying the canonical
    name, raw phrase, source sentence, and feature_exists=false.

The validator (in `negation_scope_validator.py`) reads these and
flags positive mentions in agent text. A persona may still discuss
the alternative mechanism the brief affirms — only POSITIVE
mentions of an explicitly forbidden feature are flagged.

Universal — no per-product hardcoding. CalmCue, GlowPlate,
PantryPulse, etc. are test fixtures, not branches in this file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ForbiddenFeature:
    """A feature the brief explicitly denies the product has.

    Attributes:
        canonical_name: lowercase, article-stripped noun phrase the
            validator matches against agent text ("camera",
            "microphone", "gps", "medical device").
        raw_phrase: the noun phrase as the founder wrote it
            ("a tiny camera", "GPS receiver").
        source_sentence: the full source sentence so the prompt can
            show personas where the fact came from.
        feature_exists: always False — semantic redundancy so the
            prompt block can render `feature_exists=false` plainly.
        feature_forbidden: always True — the validator uses this
            field name explicitly in flag messages.
        match_kind: one of "does_not_have", "does_not_use",
            "does_not_record", "does_not_listen",
            "does_not_capture", "is_not_a", "no_X". Helpful for
            grouping the audit.
    """

    canonical_name: str
    raw_phrase: str
    source_sentence: str
    match_kind: str
    feature_exists: bool = False
    feature_forbidden: bool = True


# ---------------------------------------------------------------------------
# Stopwords we never treat as forbidden features even if the brief
# says "no X". These would otherwise produce nonsense canonical
# names that flag legitimate persona language.
# ---------------------------------------------------------------------------

_FEATURE_STOPWORDS: frozenset[str] = frozenset({
    # generic determiners / quantifiers
    "one", "two", "three", "four", "five", "many", "some", "any",
    "more", "fewer", "less", "all", "every", "each", "such",
    # generic intensifiers and small words
    "way", "ways", "thing", "things", "stuff", "kind", "sort",
    "case", "matter", "issue", "problem", "doubt", "real", "extra",
    # negations / boolean fillers
    "yes", "ok", "okay", "good", "bad", "still", "just", "also",
    # marketing / abstract concepts that aren't features
    "promise", "guarantee", "magic", "claim", "claims",
    "fan", "fans", "fanbase",
    # numbers + units that aren't features
    "hours", "minutes", "seconds", "days", "weeks", "months", "years",
    "cents", "dollars",
    # filler verbs/words that show up at sentence starts
    "longer", "shorter", "later", "earlier", "again", "ever",
    # words from brief context that aren't features
    "customers", "buyers", "users", "user", "people",
    "reviewers", "testimonials", "reviews", "review", "buyer",
})


# Words that, if present in the canonical name, mean we should skip
# the match entirely. E.g. "no doubt" → skip, "no real customers"
# → skip (handled by no_real_customers / launch-state lock, not by
# the forbidden-feature lock).
_SKIP_IF_CONTAINS: frozenset[str] = frozenset({
    "doubt", "thanks", "kidding", "joke", "lie", "way around",
})


# ---------------------------------------------------------------------------
# Sentence splitter — mirrors the helper in caveat_leak.py.
# ---------------------------------------------------------------------------


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    placeholder = "<<DOT>>"
    safe = re.sub(
        r"(\d)\.(\d)", lambda m: f"{m.group(1)}{placeholder}{m.group(2)}",
        text,
    )
    raw = re.split(r"(?<=[\.\?\!])\s+|\n+", safe)
    return [p.replace(placeholder, ".").strip() for p in raw if p.strip()]


# ---------------------------------------------------------------------------
# Negation patterns. Each matches a feature noun phrase via the
# named group `feature`. Patterns are ordered roughly by strength —
# strong patterns ("does not have X") run first; the broader
# "no X" fallback runs last and has tighter stopword filtering.
# ---------------------------------------------------------------------------


_NEGATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "does not have X" / "doesn't have X" / "do not have X"
    (re.compile(
        r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t)\s+have\s+"
        r"(?:(?:a|an|any|the)\s+)?"
        r"(?P<feature>[a-z][a-z0-9\-' ]{1,60}?)\b"
        r"(?=[\.,;:\?\!]|\s+(?:and|or|but|because|so|when|if|"
        r"to|nor)\b|$)",
        re.IGNORECASE,
    ), "does_not_have"),
    # "does not use X" / "doesn't use X"
    (re.compile(
        r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t)\s+use\s+"
        r"(?:(?:a|an|any|the)\s+)?"
        r"(?P<feature>[a-z][a-z0-9\-' ]{1,60}?)\b"
        r"(?=[\.,;:\?\!]|\s+(?:and|or|but|because|so|when|if|"
        r"to|nor)\b|$)",
        re.IGNORECASE,
    ), "does_not_use"),
    # "does not record X"
    (re.compile(
        r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t)\s+record\s+"
        r"(?:(?:a|an|any|the)\s+)?"
        r"(?P<feature>[a-z][a-z0-9\-' ]{1,60}?)\b"
        r"(?=[\.,;:\?\!]|\s+(?:and|or|but|because|so|when|if|"
        r"to|nor)\b|$)",
        re.IGNORECASE,
    ), "does_not_record"),
    # "does not capture X" / "does not collect X"
    (re.compile(
        r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t)\s+"
        r"(?:capture|collect|store|share|stream|upload|transmit|broadcast|"
        r"livestream|monitor|identify|recognise|recognize|"
        r"diagnose|detect|track|measure)\s+"
        r"(?:(?:a|an|any|the)\s+)?"
        r"(?P<feature>[a-z][a-z0-9\-' ]{1,60}?)\b"
        r"(?=[\.,;:\?\!]|\s+(?:a|an|the|as|with|"
        r"and|or|but|because|so|when|if|to|nor)\b|$)",
        re.IGNORECASE,
    ), "does_not_capture"),
    # "does not listen" / "does not livestream" (no object — special
    # canonical-name mapping below)
    (re.compile(
        r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t)\s+"
        r"(?P<feature>listen|livestream|stream|broadcast|"
        r"track\s+location|track\s+gps)\b",
        re.IGNORECASE,
    ), "does_not_listen"),
    # "is not a X" / "is not an X" / "are not X" (category negation)
    (re.compile(
        r"\bis\s+not\s+(?:a|an)\s+"
        r"(?P<feature>[a-z][a-z0-9\-' ]{1,60}?)\b"
        r"(?=[\.,;:\?\!]|\s+(?:and|or|but|because|so|when|if|"
        r"to|nor)\b|$)",
        re.IGNORECASE,
    ), "is_not_a"),
    # "not a X" / "not an X" (category negation, looser form)
    (re.compile(
        r"(?:^|[\.,;:\!\?]\s+|\s+)not\s+(?:a|an)\s+"
        r"(?P<feature>[a-z][a-z0-9\-' ]{1,60}?)\b"
        r"(?=[\.,;:\?\!]|\s+(?:and|or|but|because|so|when|if|"
        r"to|nor)\b|$)",
        re.IGNORECASE,
    ), "is_not_a"),
    # "no X" — broad fallback. Tighter stopword filtering applied.
    (re.compile(
        r"(?:^|[\.,;:\!\?]\s+|\s+)no\s+"
        r"(?P<feature>[a-z][a-z0-9\-]{1,40})\b"
        r"(?=[\.,;:\?\!]|\s+(?:and|or|but|because|so|when|if|"
        r"to|nor|on|in|at|with|for)\b|$)",
        re.IGNORECASE,
    ), "no_X"),
    # "without X" — companion to "no X"
    (re.compile(
        r"\bwithout\s+(?:(?:a|an|any|the)\s+)?"
        r"(?P<feature>[a-z][a-z0-9\-]{1,40})\b"
        r"(?=[\.,;:\?\!]|\s+(?:and|or|but|because|so|when|if|"
        r"to|nor|on|in|at|with|for)\b|$)",
        re.IGNORECASE,
    ), "without_X"),
)


# Special canonical-name mapping for verbs that don't take an object
# in the regex (e.g. "does not listen" → canonical "audio recording").
_VERB_TO_CANONICAL: dict[str, str] = {
    "listen": "audio recording",
    "livestream": "livestreaming",
    "stream": "streaming",
    "broadcast": "broadcasting",
    "track location": "location tracking",
    "track gps": "gps tracking",
}


def _normalize_feature(raw: str) -> str | None:
    """Lowercase, strip articles, drop trailing punctuation, collapse
    whitespace. Returns None for stopword / empty matches that should
    not become a forbidden-feature entry."""
    if not raw:
        return None
    s = raw.strip().lower()
    # Drop leading articles + adjectives that don't change identity
    s = re.sub(
        r"^(?:a|an|the|any|some|tiny|small|built-?in|wide-?angle|"
        r"miniature|real|true)\s+",
        "",
        s,
    )
    s = re.sub(r"\s+", " ", s).strip(" .,;:!?'\"")
    if not s or len(s) > 60:
        return None
    # Single-word stopwords
    if s in _FEATURE_STOPWORDS:
        return None
    # Words that signal we should skip ("no doubt", "no kidding").
    head = s.split()[0]
    if head in _FEATURE_STOPWORDS:
        return None
    for skip in _SKIP_IF_CONTAINS:
        if skip in s:
            return None
    # Apply the verb-to-canonical mapping for object-less negations.
    if s in _VERB_TO_CANONICAL:
        return _VERB_TO_CANONICAL[s]
    return s


def _dedup_forbidden(
    items: Iterable[ForbiddenFeature],
) -> list[ForbiddenFeature]:
    """Deduplicate by canonical_name; first occurrence wins."""
    seen: set[str] = set()
    out: list[ForbiddenFeature] = []
    for ff in items:
        if ff.canonical_name in seen:
            continue
        seen.add(ff.canonical_name)
        out.append(ff)
    return out


def extract_forbidden_features(
    *,
    product_description: str,
    optional_context: str | None = None,
) -> list[ForbiddenFeature]:
    """Walk the brief sentence-by-sentence and pull out every
    explicit negative-feature statement."""
    blob = " ".join([product_description or "", optional_context or ""])
    sentences = _split_sentences(blob)
    out: list[ForbiddenFeature] = []
    for sent in sentences:
        for rx, kind in _NEGATION_PATTERNS:
            for m in rx.finditer(sent):
                raw = m.group("feature")
                canonical = _normalize_feature(raw)
                if canonical is None:
                    continue
                # Skip very generic "no X" / "without X" that aren't
                # really feature claims (e.g. "no extra closet
                # space", "without losing the rest").
                if kind in ("no_X", "without_X"):
                    # Require the canonical to look like a noun
                    # (length >= 3, not in stopwords) — already
                    # enforced — and skip if it would shadow a
                    # legit launch-state phrase like "no real
                    # customers".
                    if canonical in {
                        "real customers", "real users", "real buyers",
                        "real reviewers", "real testimonials",
                    }:
                        continue
                out.append(ForbiddenFeature(
                    canonical_name=canonical,
                    raw_phrase=raw.strip(),
                    source_sentence=sent.strip(),
                    match_kind=kind,
                ))
    return _dedup_forbidden(out)


# ---------------------------------------------------------------------------
# Synonym expansion — when the brief says "no camera", agents may
# refer to it as "lens", "mic", "the cam". We expand the canonical
# name into a small set of validation tokens. Universal.
# ---------------------------------------------------------------------------


_SYNONYMS: dict[str, tuple[str, ...]] = {
    "camera": ("camera", "cam", "lens", "video sensor", "image sensor"),
    "microphone": ("microphone", "mic", "mics", "audio input", "voice input"),
    "audio recording": ("audio recording", "voice recording",
                        "record audio", "recording audio"),
    "video recording": ("video recording", "record video",
                        "recording video"),
    "livestreaming": ("livestream", "livestreaming", "live stream",
                      "streaming live"),
    "gps": ("gps", "gps receiver", "gps chip", "gps tracking",
            "location chip"),
    "location tracking": ("location tracking", "track your location",
                          "tracks location"),
    "speaker": ("speaker", "speakers", "loudspeaker"),
    "screen": ("screen", "display"),
    "facial recognition": ("facial recognition", "face recognition",
                           "face id", "face-id"),
    "medical device": ("medical device", "medical-device", "medical product"),
    "diagnosis": ("diagnose", "diagnoses", "diagnosing"),
}


def expand_forbidden_tokens(forbidden: ForbiddenFeature) -> tuple[str, ...]:
    """Return the validation tokens to search for in agent text for
    this forbidden feature. Falls back to just the canonical_name if
    no synonym entry exists."""
    base = forbidden.canonical_name
    if base in _SYNONYMS:
        return _SYNONYMS[base]
    # Also accept the raw phrase as a token (e.g. "tiny camera" — the
    # canonical strips "tiny", but the raw phrase may match in some
    # agent text).
    return (base,)
