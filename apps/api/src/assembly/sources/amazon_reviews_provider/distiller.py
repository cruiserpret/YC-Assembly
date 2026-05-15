"""Phase 11A — rule-based distiller: raw review → buyer-language signal.

This is the first-pass distiller. It's deliberately rule-based, not
LLM-based, so:

  * Phase 11A can ship without burning a single LLM call.
  * Operator can audit *why* a signal was extracted (every signal
    carries the regex pattern that fired in `theme`).
  * Phase 11B ingestion can run over millions of reviews offline at
    near-zero cost.
  * A future Phase-11x LLM distiller can layer on top without
    replacing this scaffold.

Distillation strategy (per accepted review):
  - 1–2 star reviews → `objection`, `return_reason`, durability /
    safety / setup / support specifics if they match patterns.
  - 4–5 star reviews → `praise`, `proof_need` (if review explicitly
    *checked* a proof need), use-case signals, durability /
    longevity, switch-from-X signals.
  - 3-star or no rating → `mixed` sentiment, only signals that
    explicitly fire (typically `price` complaints, `setup`
    confusion).
  - `price` / `trust` / `safety` / `setup` / `support` /
    `durability` patterns fire from any star rating that matches.

Each distilled signal stores only a SHORT snippet
(≤ `short_snippet_max_chars`, default 240) — never the full review
body. The model has a Text column for safety but the distiller never
fills more than the configured cap.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Iterator, Pattern

from assembly.sources.amazon_reviews_2023 import AmazonReviewRecord
from assembly.sources.amazon_reviews_provider.signal_types import (
    SentimentBucket,
    SignalType,
)

# ---------------------------------------------------------------------------
# Config + dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DistillerConfig:
    """Tunable thresholds for the distiller."""

    min_review_chars: int = 40
    short_snippet_max_chars: int = 240
    # Cap on signals per review — keeps a single ranty 5-star review
    # from flooding the table with 30 rows.
    max_signals_per_review: int = 5


@dataclass(frozen=True)
class DistilledSignal:
    """One distilled buyer-language signal extracted from one raw
    review row. Maps 1:1 onto a row in the `amazon_review_signal`
    table.
    """

    source_dataset: str
    category: str
    product_title: str | None
    brand: str | None
    asin: str | None
    parent_asin: str | None
    rating: int | None
    review_timestamp: int | None
    verified_purchase: bool | None
    helpful_votes: int | None
    sentiment_bucket: SentimentBucket
    signal_type: SignalType
    theme: str | None
    short_snippet: str
    competitor_mention: str | None
    use_case: str | None
    source_review_hash: str
    # Free-form metadata for the test layer; never persisted.
    debug: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pattern library — every entry must be product-agnostic
# ---------------------------------------------------------------------------
#
# Patterns are intentionally generic. They MUST NOT name a brand or
# product category — the *review text* names the brand/category, the
# distiller just detects the *shape* of a buyer complaint or praise.


_def_flags = re.IGNORECASE


@dataclass(frozen=True)
class _Rule:
    name: str
    signal_type: SignalType
    pattern: Pattern[str]
    theme: str | None = None


def _r(pattern: str) -> Pattern[str]:
    return re.compile(pattern, _def_flags)


# --- objections / return reasons / failure modes (typically 1–2 star) ----
_NEG_RULES: tuple[_Rule, ...] = (
    _Rule(
        "returned_it",
        "return_reason",
        _r(r"\b(returned|sent\s+it\s+back|asked\s+for\s+a\s+refund)\b"),
        "returned_product",
    ),
    _Rule(
        "broke_after",
        "durability",
        _r(r"\b(broke|cracked|stopped\s+working|died|fell\s+apart)\b"),
        "broke_or_died",
    ),
    _Rule(
        "battery_runtime",
        "durability",
        _r(
            r"\b(battery|charge|runtime)\b[^.]{0,40}?\b("
            r"dies|drains|drained|doesn'?t\s+last|barely\s+lasts|"
            r"only\s+lasts|short\s+life)\b",
        ),
        "battery_runtime",
    ),
    _Rule(
        "too_expensive",
        "price",
        _r(
            r"\b(too\s+(expensive|pricey|much)|overpriced|"
            r"not\s+worth\s+(the\s+)?(price|money)|"
            r"highway\s+robbery)\b",
        ),
        "price_objection",
    ),
    _Rule(
        "hard_to_setup",
        "setup",
        _r(
            r"\b(hard|impossible|confusing|frustrating)\s+to\s+"
            r"(set\s*up|install|configure|pair|connect)\b",
        ),
        "setup_friction",
    ),
    _Rule(
        "instructions_unclear",
        "setup",
        _r(
            r"\b(instructions|manual|directions)\b[^.]{0,30}?\b("
            r"unclear|terrible|missing|useless|hard\s+to\s+follow)\b",
        ),
        "instructions",
    ),
    _Rule(
        "support_unresponsive",
        "support",
        _r(
            r"\b(customer\s+(service|support)|support\s+team)\b"
            r"[^.]{0,40}?\b("
            r"unresponsive|never\s+(replied|responded)|terrible|"
            r"useless|nightmare)\b",
        ),
        "support_unresponsive",
    ),
    _Rule(
        "not_safe",
        "safety",
        _r(
            r"\b(burned|caught\s+fire|sparked|electric\s+shock|"
            r"unsafe|dangerous|hazard)\b",
        ),
        "safety_concern",
    ),
    _Rule(
        "trust_data_privacy",
        "trust",
        _r(
            r"\b(privacy|data\s+collection|tracks?\s+me|sells?\s+"
            r"(my\s+)?data|spying|listens\s+to\s+me)\b",
        ),
        "privacy_concern",
    ),
    _Rule(
        "feels_cheap",
        "durability",
        _r(r"\b(feels?\s+(cheap|flimsy)|cheaply\s+made|plasticky)\b"),
        "build_quality",
    ),
    # ----- Phase 11B.5 SETUP recall broadening ----------------------
    # The original `hard_to_setup` + `instructions_unclear` rules above
    # only caught one specific phrasing. Real reviewers describe setup
    # friction much more loosely; these patterns cover the common
    # alternate shapes without firing on generic complaints.
    _Rule(
        "took_forever_setup",
        "setup",
        _r(
            r"\btook\s+(?:me\s+|us\s+)?"
            r"(forever|hours|days|weeks|ages|all\s+(?:day|night|weekend))\s+to\s+"
            r"(install|set\s*up|figure\s+(?:it|this|out)|"
            r"get\s+(?:it|this|the\s+\w+)\s+(?:working|to\s+work)|"
            r"configure|activate|pair|connect|sync)\b",
        ),
        "setup_time_excessive",
    ),
    _Rule(
        "couldnt_get_working",
        "setup",
        _r(
            r"\bcould(?:n'?t|\s+not)\s+"
            r"(get\s+(?:it|this|the\s+\w+)\s+to\s+work|"
            r"figure\s+(?:it|this|out|out\s+how)|"
            r"install\s+(?:it|this)|set\s+(?:it|this)\s+up|"
            r"activate\s+(?:it|this)|"
            r"pair\s+(?:it|this)|connect\s+(?:it|this))\b",
        ),
        "couldnt_setup",
    ),
    _Rule(
        "install_nightmare",
        "setup",
        _r(
            r"\b(install(?:ation|ing)?|setup|set\s+up|"
            r"configuration|activation|pairing)\s+"
            r"(?:process\s+)?"
            r"(?:was|is|has\s+been)\s+"
            r"(a\s+)?(nightmare|painful|hell|disaster|brutal|"
            r"excruciating|infuriating)\b",
        ),
        "setup_nightmare",
    ),
    _Rule(
        "setup_failed",
        "setup",
        _r(
            r"\b(activation|setup|configuration|installation|pairing|"
            r"connecting|syncing|registration|sign[\s-]?up)\s+"
            r"(fail(?:ed|s)|won'?t\s+(?:work|complete|finish)|"
            r"wouldn'?t\s+(?:work|complete|finish)|"
            r"never\s+(?:works|worked|completed))\b",
        ),
        "setup_failed",
    ),
    # ----- Phase 11B.5 SUPPORT recall broadening --------------------
    _Rule(
        "support_useless_loose",
        "support",
        _r(
            r"\b(?:customer\s+)?(support|customer\s+service|customer\s+care|cs\s+team)\s+"
            r"(?:is|was|are|were)\s+"
            r"(useless|terrible|garbage|awful|horrible|trash|"
            r"a\s+(?:joke|nightmare|disaster|waste))\b",
        ),
        "support_useless",
    ),
    _Rule(
        "no_help_from_support",
        "support",
        _r(
            r"\bno\s+(help|response|reply|answer)\s+from\s+"
            r"(support|customer\s+service|the\s+(?:seller|manufacturer|"
            r"company|vendor))\b",
        ),
        "support_no_response",
    ),
    _Rule(
        "seller_wouldnt_help",
        "support",
        _r(
            r"\b(seller|manufacturer|company|vendor|merchant)\s+"
            r"(wouldn'?t|won'?t|refused\s+to|did\s+not|didn'?t)\s+"
            r"(help|respond|reply|honor|cover|fix|replace|refund)\b",
        ),
        "seller_uncooperative",
    ),
    _Rule(
        "repeated_contact_attempts",
        "support",
        _r(
            r"\b(called|emailed|contacted|messaged|reached\s+out\s+to)\s+"
            r"(?:them\s+|support\s+|the\s+seller\s+|customer\s+service\s+)?"
            r"(\d+\s+times|"
            r"(?:three|four|five|six|seven|eight|nine|ten|a\s+dozen|dozens\s+of)\s+times|"
            r"multiple\s+times|several\s+times|"
            r"many\s+times|over\s+and\s+over|repeatedly)\b",
        ),
        "repeated_contact_attempts",
    ),
    _Rule(
        "warranty_refund_denied",
        "support",
        _r(
            r"\b(warranty|refund|return)\s+"
            r"(?:was|is|process\s+(?:was|is))?\s*"
            r"(denied|refused|rejected|impossible|a\s+nightmare|"
            r"wouldn'?t\s+honor|won'?t\s+honor|never\s+honored)\b",
        ),
        "warranty_or_return_denied",
    ),
    # ----- Phase 11B.5 TRUST recall broadening ----------------------
    _Rule(
        "dont_trust_brand",
        "trust",
        _r(
            r"\b(don'?t|do\s+not|never|wouldn'?t|will\s+never|"
            r"would\s+never)\s+trust\s+"
            r"(this|that|the|any\s+more|any\s+of)\s*"
            r"(brand|company|seller|product|listing|store|manufacturer)?\b",
        ),
        "explicit_distrust",
    ),
    _Rule(
        "feels_scammy",
        "trust",
        _r(
            r"\b(feels|seems|looks|sounds|smells)\s+"
            r"(?:like\s+(?:a\s+)?)?"
            r"(scammy|sketchy|shady|fishy|suspicious|scam)\b",
        ),
        "scam_suspicion",
    ),
    _Rule(
        "counterfeit_or_fake",
        "trust",
        _r(
            # Explicit-claim shapes only. Bare "knockoff" / "fake" /
            # "counterfeit" appear constantly in PRAISE contexts
            # ("not a knockoff", "not a cheap counterfeit"), so the
            # rule requires either an identifying verb/adverb in front
            # OR an explicit "not authentic / genuine / the real X"
            # phrasing.
            r"\b("
            r"(?:appears|seems|looks|definitely|clearly|"
            r"this\s+is|it'?s|"
            r"i\s+got|i\s+received|i\s+ordered|got|received|"
            r"they\s+sent\s+(?:me\s+)?|sent\s+me)"
            r"\s+(?:to\s+be\s+)?(?:a\s+)?"
            r"(?:cheap\s+)?"
            r"(?:fake|counterfeit|knockoff|knock[-\s]off|bootleg)"
            r"|"
            r"not\s+(?:authentic|genuine|the\s+real\s+\w+)"
            r"|"
            r"complete(?:ly)?\s+(?:fake|counterfeit|knockoff|bootleg)"
            r"|"
            r"obvious(?:ly)?\s+(?:fake|counterfeit|knockoff|bootleg)"
            r")\b",
        ),
        "counterfeit_concern",
    ),
    _Rule(
        "misleading_listing",
        "trust",
        _r(
            r"\b("
            r"misleading\s+(?:listing|description|product|advertising|ad)|"
            r"not\s+as\s+(?:advertised|described|pictured|shown|listed)|"
            r"false\s+advertising|"
            r"bait\s+(?:and|&)\s+switch"
            r")\b",
        ),
        "misleading_listing",
    ),
    _Rule(
        "fake_reviews_suspicion",
        "trust",
        _r(
            r"\b("
            r"fake\s+reviews?|paid\s+reviews?|"
            r"review\s+(?:farm|farming|manipulation)|"
            r"reviews\s+(?:are|seem|look|feel|must\s+be)\s+"
            r"(?:fake|paid|fishy|suspicious|bots)"
            r")\b",
        ),
        "fake_reviews_suspicion",
    ),
    # Generic-complaint catch-all. This fires on common buyer-objection
    # shapes that don't already fit a more specific bucket (return /
    # durability / price / setup / support / safety / trust). Placed
    # last so the more specific rules always win their slot first.
    _Rule(
        "generic_objection",
        "objection",
        _r(
            r"\b(disappointing|disappointed|waste\s+of\s+(time|money|space)|"
            r"absolutely\s+nothing|does(?:n'?t|\s+not)\s+(work|do\s+what)|"
            r"not\s+what\s+(?:i|we)\s+(?:expected|wanted))\b",
        ),
        "generic_disappointment",
    ),
)


# --- praise / proof / switch-from / use-case (typically 4–5 star) ---------
_POS_RULES: tuple[_Rule, ...] = (
    _Rule(
        "would_buy_again",
        "praise",
        _r(r"\b(would\s+(buy|recommend|purchase)\s+(it\s+)?again)\b"),
        "would_buy_again",
    ),
    _Rule(
        "love_it",
        "praise",
        _r(
            r"\b(love\s+(it|this)|absolutely\s+(love|amazing)|"
            r"game[-\s]?changer|life\s+saver|best\s+purchase)\b",
        ),
        "general_praise",
    ),
    _Rule(
        "switched_from",
        "switch_reason",
        _r(
            r"\b(?:switched\s+from|upgraded\s+from|replaced\s+my)\s+"
            r"([A-Z][\w\- ]{2,40})",
        ),
        "switched_from_competitor",
    ),
    _Rule(
        "better_than",
        "switch_reason",
        _r(
            r"\b(?:better\s+than|much\s+better\s+than)\s+"
            r"([A-Z][\w\- ]{2,40})",
        ),
        "comparison_better_than",
    ),
    _Rule(
        "proof_it_works",
        "proof_need",
        _r(
            r"\b(after\s+\d+\s+(days|weeks|months)|"
            r"used\s+(it\s+)?(daily|every\s+day|for\s+months))\b",
        ),
        "longitudinal_use",
    ),
    _Rule(
        "use_case_specific",
        "use_case",
        _r(
            r"\b(?:use\s+(?:it|this)\s+for|perfect\s+for|great\s+for)\s+"
            r"([\w\- ]{3,40})",
        ),
        "stated_use_case",
    ),
    _Rule(
        "holds_up",
        "durability",
        _r(
            r"\b(still\s+(works|going)|holds\s+up|years\s+of\s+use|"
            r"built\s+to\s+last|sturdy|well[-\s]?made|solid\s+build)\b",
        ),
        "durability_praise",
    ),
    _Rule(
        "worth_the_price",
        "price",
        _r(
            r"\b(worth\s+(every\s+penny|the\s+(price|money))|"
            r"great\s+value|paid\s+for\s+itself)\b",
        ),
        "value_praise",
    ),
)


# Tokens that, if any of them appear in a "switched_from" / "better_than"
# capture group, mean we did NOT actually capture a competitor name —
# just a generic English phrase the regex over-matched. Filtering these
# out prevents noise like `switched from My Old Routine` ending up as a
# competitor mention.
_COMPETITOR_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "this", "that", "those", "these", "my", "our",
        "their", "his", "her", "before", "old", "previous", "other",
        "another", "any", "some", "one", "two", "three", "amazon",
    }
)


def _is_competitor_name(raw: str) -> bool:
    """Loose filter: capture-group looks like a real product/brand,
    not a generic English phrase."""
    raw = raw.strip().strip(",.;:")
    if not raw:
        return False
    lowered = raw.lower()
    first_token = lowered.split()[0] if lowered.split() else ""
    if first_token in _COMPETITOR_STOPWORDS:
        return False
    # Must have at least one capitalized token to look brand-like.
    return any(tok[:1].isupper() for tok in raw.split() if tok)


# ---------------------------------------------------------------------------
# Eligibility check (sister of low-quality-review check)
# ---------------------------------------------------------------------------


def is_review_eligible(
    record: AmazonReviewRecord,
    config: DistillerConfig,
) -> tuple[bool, str | None]:
    """Cheap pre-filter — return (ok, rejection_reason).

    Re-uses the Phase 8.5A low-quality filter's spirit but exposes the
    rejection reason so Phase 11B ingestion can log accept/reject
    counts by reason (per operator spec).
    """
    text = (record.text or "").strip()
    if not text:
        return False, "empty_text"
    if len(text) < config.min_review_chars:
        return False, "too_short"
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 20:
        upper = sum(1 for c in letters if c.isupper())
        if upper / max(len(letters), 1) >= 0.85:
            return False, "all_caps_spam"
    if record.rating is None:
        # We accept ratingless reviews but they only get "mixed" signals.
        return True, None
    if not 1.0 <= record.rating <= 5.0:
        return False, "rating_out_of_range"
    return True, None


# ---------------------------------------------------------------------------
# Distiller — main entry point
# ---------------------------------------------------------------------------


def _sentiment_for(rating: int | None) -> SentimentBucket:
    if rating is None:
        return "mixed"
    if rating <= 2:
        return "negative"
    if rating >= 4:
        return "positive"
    return "mixed"


def _short_snippet(text: str, max_chars: int) -> str:
    """Return a tight snippet from `text`, never longer than
    `max_chars`. We try to keep the snippet at a sentence boundary so
    it reads cleanly in the model's `short_snippet` column."""
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Prefer cutting at the last sentence boundary inside the window.
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx >= int(max_chars * 0.5):
            return cut[: idx + 1].strip()
    return cut.rstrip() + "…"


def _hash_review(
    record: AmazonReviewRecord,
) -> str:
    """Stable identity hash for the source review row. SHA-256 first
    16 hex chars of (category|asin|user_id_hash|timestamp|first 128 chars
    of text) — sufficient for cross-run de-dup, insufficient for
    re-identification."""
    parts = (
        record.category or "",
        record.asin or record.parent_asin or "",
        record.user_id_hash or "",
        str(record.timestamp or 0),
        (record.text or "")[:128],
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _looks_like_use_case_phrase(raw: str | None) -> bool:
    if not raw:
        return False
    raw = raw.strip().strip(",.;:")
    if len(raw) < 3:
        return False
    # Reject empty captures or single function words.
    if raw.lower() in {"it", "this", "that"}:
        return False
    return True


def _fire_rules(
    rules: Iterable[_Rule],
    text: str,
) -> Iterator[tuple[_Rule, re.Match[str]]]:
    for rule in rules:
        m = rule.pattern.search(text)
        if m is not None:
            yield rule, m


def distill_review_signals(
    record: AmazonReviewRecord,
    *,
    config: DistillerConfig | None = None,
    source_dataset: str = "amazon_reviews_2023",
    product_title: str | None = None,
    brand: str | None = None,
) -> list[DistilledSignal]:
    """Convert one accepted raw review into a list of distilled signals.

    Returns an empty list when:
      * the review fails `is_review_eligible`
      * no rule fires (the review is genuine but doesn't carry any of
        the buyer-language patterns we recognize in Phase 11A)
    """
    cfg = config or DistillerConfig()
    ok, _reason = is_review_eligible(record, cfg)
    if not ok:
        return []

    text = (record.text or "").strip()
    rating_int = int(record.rating) if record.rating is not None else None
    sentiment = _sentiment_for(rating_int)
    review_hash = _hash_review(record)
    snippet = _short_snippet(text, cfg.short_snippet_max_chars)

    # Choose which rule sets to evaluate. We always run BOTH sets — a
    # 4-star review can contain a `setup` objection, a 2-star review
    # can contain a `would buy again from competitor` phrase, etc. The
    # sentiment_bucket reflects the overall rating, not the individual
    # signal.
    rules: list[_Rule] = list(_NEG_RULES) + list(_POS_RULES)

    out: list[DistilledSignal] = []
    seen_signal_types: set[SignalType] = set()

    for rule, match in _fire_rules(rules, text):
        if rule.signal_type in seen_signal_types:
            # One signal per type per review keeps the table from
            # blowing up on ranty reviews.
            continue

        competitor: str | None = None
        use_case: str | None = None

        # Both `switched_from`/`better_than` and `use_case_specific`
        # use a single capture group at index 1 (their inner verb
        # alternations are non-capturing). Defaulting to "" keeps the
        # filter helpers safe even if a future rule re-introduces a
        # nested group.
        if rule.name in {"switched_from", "better_than"}:
            cand = match.group(1) if match.lastindex and match.lastindex >= 1 else ""
            if _is_competitor_name(cand or ""):
                competitor = cand.strip().strip(",.;:")[:128]
            else:
                continue  # the regex over-matched a generic phrase.

        if rule.name == "use_case_specific":
            cand = match.group(1) if match.lastindex and match.lastindex >= 1 else ""
            if _looks_like_use_case_phrase(cand):
                use_case = (cand or "").strip().strip(",.;:")[:128]
            else:
                continue

        out.append(
            DistilledSignal(
                source_dataset=source_dataset,
                category=record.category,
                product_title=product_title,
                brand=brand,
                asin=record.asin,
                parent_asin=record.parent_asin,
                rating=rating_int,
                review_timestamp=record.timestamp,
                verified_purchase=record.verified_purchase,
                helpful_votes=record.helpful_vote,
                sentiment_bucket=sentiment,
                signal_type=rule.signal_type,
                theme=rule.theme,
                short_snippet=snippet,
                competitor_mention=competitor,
                use_case=use_case,
                source_review_hash=review_hash,
                debug={"rule": rule.name},
            ),
        )
        seen_signal_types.add(rule.signal_type)

        if len(out) >= cfg.max_signals_per_review:
            break

    return out
