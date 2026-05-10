"""Phase 8.5B — tightened filters + metadata join + confidence scoring
for the Amazon Reviews 2023 local adapter.

Phase 8.5A's preflight surfaced two systemic false-positive shapes:

  1. Bare `Prime` matches the Amazon Prime SHIPPING service rather
     than Prime Energy / Prime Hydration drinks. 7 of 10 Sports
     matches were shipping-context.

  2. Bare `flavor` matches generic food/tea/seasoning reviews with
     no drink-category context. ~70%+ of Grocery's `flavor` hits
     were tea/baking/condiment.

Phase 8.5B replaces the term-substring filter with:

  * `MetadataIndex` — streams the per-category metadata file ONCE,
    keeping only entries whose `parent_asin` is in the candidate
    set. Drops `images` and `videos` at parse time. Returns a
    minimal `AmazonProductMetadata` per asin.

  * `score_review` — deterministic scorer over BOTH review text
    AND joined product metadata. Returns a `ReviewConfidence`
    closed-enum label plus a structured detail breakdown.

  * `prime_context_classification` — a focused helper that
    distinguishes Prime-as-drink from Prime-as-shipping-service.

  * `flavor_qualifies` — a focused helper that requires `flavor`
    to co-occur with a drink/fitness/caffeine category anchor.

NO Amazon API. NO Amazon.com scraping. Drift-tested.
"""
from __future__ import annotations

import enum
import gzip
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assembly.sources.amazon_reviews_2023.adapter import AmazonReviewRecord


# ---------------------------------------------------------------------------
# Closed enum for confidence label
# ---------------------------------------------------------------------------


class ReviewConfidence(str, enum.Enum):
    HIGH_CONFIDENCE = "high_confidence"
    MEDIUM_CONFIDENCE = "medium_confidence"
    LOW_CONFIDENCE = "low_confidence"
    REJECTED = "rejected"


class PrimeContext(str, enum.Enum):
    DRINK = "drink"          # Prime Energy / Hydration / sports drink
    SHIPPING = "shipping"    # Amazon Prime shipping/service
    AMBIGUOUS = "ambiguous"  # Has "Prime" but neither shape clearly


# ---------------------------------------------------------------------------
# Term lexicons (closed lists — no LLM)
# ---------------------------------------------------------------------------


# Strong drink-category anchors. Worth +3 in scoring.
_STRONG_CATEGORY_ANCHORS = (
    "energy drink", "sports drink", "pre-workout", "pre workout",
    "preworkout", "electrolyte",
)

# Medium drink-category anchors. Worth +2.
_MEDIUM_CATEGORY_ANCHORS = (
    "caffeine", "caffeinated", "hydration", "supplement", "protein",
    "sugar free", "sugar-free", "low sugar",
)

# Weak drink-category anchors. Worth +1.
_WEAK_CATEGORY_ANCHORS = (
    "workout", "gym", "endurance", "recovery", "crash",
    "athletic", "fitness",
)

# Named energy/sports/hydration brands. Worth +2 each (when in
# drink-context — Prime is special-cased separately).
_NAMED_BRANDS_DRINK = (
    "Celsius", "Monster", "Red Bull", "Gatorade", "Powerade",
    "Body Armor", "Liquid I.V.", "Liquid IV", "Bang", "Reign",
    "Rockstar", "C4", "Ghost", "NOS",
)

# Prime drink-context phrases. If any appears, the review's "Prime"
# match is classified as DRINK.
_PRIME_DRINK_PHRASES = (
    "prime energy", "prime hydration", "prime drink",
    "prime sports drink", "prime beverage",
    "logan paul prime", "ksi prime", "prime energy drink",
    "prime hydration drink",
)

# Prime shipping/service-context phrases. If any appears AND no
# Prime drink phrase is found, the review's "Prime" match is
# classified as SHIPPING.
_PRIME_SHIPPING_PHRASES = (
    "amazon prime", "prime shipping", "i have prime", "prime delivery",
    "prime membership", "prime eligible", "prime day", "prime video",
    "package arrived", "fast shipping", "prime member",
    "with prime", "use prime", "got prime", "free prime",
    "prime two-day", "prime 2-day", "prime 2 day",
    "arrived quickly",  # shipping-context phrase per spec
)

# Categories in metadata.categories[] or main_category that
# strongly indicate the product is a drink/supplement/sports
# beverage. Worth +2.
_METADATA_CATEGORY_ANCHORS = (
    "energy drink", "sports drink", "beverages", "drinks",
    "sports nutrition", "pre-workout", "pre workout",
    "electrolyte", "hydration", "supplement",
    "protein", "vitamin", "energy & sports drinks",
)

# Tightened search-term list for term-hit reporting (replaces the
# 8.5A list which had bare "Prime" and unconditional "flavor").
TIGHTENED_SEARCH_TERMS: tuple[str, ...] = (
    "energy drink", "sports drink", "pre-workout", "pre workout",
    "electrolyte", "hydration",
    "caffeine", "caffeinated", "supplement", "protein",
    "sugar free", "sugar-free", "low sugar", "crash",
    "workout", "gym", "endurance", "recovery",
    # Named brands (drink-only):
    "Red Bull", "Monster", "Celsius", "Gatorade",
    "Prime Energy", "Prime Hydration", "Prime Energy drink",
    # Conditional terms (require a category anchor co-occurrence):
    "flavor",
)


# ---------------------------------------------------------------------------
# Metadata loading (memory-efficient, drops privacy-sensitive fields)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmazonProductMetadata:
    """Normalized product metadata for one parent_asin.

    `images`, `videos`, and `bought_together` are NEVER stored —
    privacy-policy-sensitive fields the dataset surfaces are dropped
    at parse time per the compliance memo."""
    parent_asin: str
    title: str = ""
    store: str = ""
    main_category: str = ""
    categories: tuple[str, ...] = ()
    description: str = ""
    features: tuple[str, ...] = ()
    price: float | None = None
    average_rating: float | None = None
    rating_number: int | None = None
    details_summary: dict[str, str] = field(default_factory=dict)


def _flatten_description(raw: Any) -> str:
    """The dataset stores `description` as a list-of-strings sometimes
    and a string sometimes. Normalize to a single bounded string."""
    if isinstance(raw, list):
        out = " ".join(str(x) for x in raw if x).strip()
    elif isinstance(raw, str):
        out = raw.strip()
    else:
        out = ""
    return out[:1500]  # bounded so audit JSON stays manageable


def _flatten_features(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(x).strip() for x in raw if x)[:10]


def _flatten_categories(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(x).strip() for x in raw if x)[:8]


def _flatten_details(raw: Any) -> dict[str, str]:
    """Keep only string-valued, non-image-URL details."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if "image" in k.lower() or "url" in k.lower():
            continue
        if "http" in v.lower() and ("amazon" in v.lower() or ".jpg" in v.lower()):
            continue
        out[k[:40]] = v[:200]
        if len(out) >= 10:
            break
    return out


def _parse_metadata_record(obj: dict[str, Any]) -> AmazonProductMetadata | None:
    asin = obj.get("parent_asin")
    if not isinstance(asin, str) or not asin:
        return None
    price = obj.get("price")
    try:
        price_f = float(price) if isinstance(price, (int, float, str)) and price not in ("", None) else None
    except (TypeError, ValueError):
        price_f = None
    avg = obj.get("average_rating")
    try:
        avg_f = float(avg) if avg is not None else None
    except (TypeError, ValueError):
        avg_f = None
    rn = obj.get("rating_number")
    try:
        rn_i = int(rn) if rn is not None else None
    except (TypeError, ValueError):
        rn_i = None
    return AmazonProductMetadata(
        parent_asin=asin,
        title=str(obj.get("title") or "")[:300],
        store=str(obj.get("store") or "")[:120],
        main_category=str(obj.get("main_category") or "")[:80],
        categories=_flatten_categories(obj.get("categories")),
        description=_flatten_description(obj.get("description")),
        features=_flatten_features(obj.get("features")),
        price=price_f,
        average_rating=avg_f,
        rating_number=rn_i,
        details_summary=_flatten_details(obj.get("details")),
    )


class MetadataIndex:
    """Streaming metadata reader that retains ONLY the entries whose
    parent_asin is in the supplied candidate set.

    Memory cost: O(|target_asins|) — at most one minimal metadata
    record per requested asin. The full metadata file is NEVER
    fully loaded; the streaming pass short-circuits as soon as
    every target asin has been resolved.
    """

    def __init__(
        self,
        *,
        meta_file: Path,
        target_asins: set[str],
    ) -> None:
        self._meta_file = meta_file
        self._target_asins = set(target_asins)
        self._index: dict[str, AmazonProductMetadata] = {}
        self._scanned_lines: int = 0
        self._loaded = False

    @property
    def index(self) -> dict[str, AmazonProductMetadata]:
        return self._index

    @property
    def lines_scanned(self) -> int:
        return self._scanned_lines

    def load(self) -> None:
        """One-pass stream. Short-circuits when every target asin is
        found OR when the file is exhausted."""
        if self._loaded:
            return
        self._loaded = True
        if not self._meta_file.is_file() or not self._target_asins:
            return
        opener = gzip.open if self._meta_file.suffix == ".gz" else open
        try:
            with opener(self._meta_file, "rt", encoding="utf-8") as fh:  # type: ignore[arg-type]
                for raw in fh:
                    self._scanned_lines += 1
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    asin = obj.get("parent_asin")
                    if not isinstance(asin, str):
                        continue
                    if asin not in self._target_asins:
                        continue
                    parsed = _parse_metadata_record(obj)
                    if parsed is not None:
                        self._index[asin] = parsed
                    if len(self._index) == len(self._target_asins):
                        return
        except OSError:
            return

    def lookup(self, parent_asin: str | None) -> AmazonProductMetadata | None:
        if not parent_asin:
            return None
        return self._index.get(parent_asin)


# ---------------------------------------------------------------------------
# Prime context + flavor qualification
# ---------------------------------------------------------------------------


_PRIME_RE = re.compile(r"\bPrime\b", re.IGNORECASE)


def prime_context_classification(text: str) -> PrimeContext:
    """Classify the role of any "Prime" mention in `text`.

    Returns:
      DRINK   — text contains a Prime-as-drink phrase (Prime Energy,
                Prime Hydration, etc.) somewhere.
      SHIPPING — text contains a Prime-as-service phrase AND no
                 Prime-as-drink phrase.
      AMBIGUOUS — text contains "Prime" but neither shape phrase.
    """
    low = (text or "").lower()
    if any(p in low for p in _PRIME_DRINK_PHRASES):
        return PrimeContext.DRINK
    if any(p in low for p in _PRIME_SHIPPING_PHRASES):
        return PrimeContext.SHIPPING
    if _PRIME_RE.search(text or ""):
        return PrimeContext.AMBIGUOUS
    return PrimeContext.AMBIGUOUS  # treat no-Prime same as ambiguous


def flavor_qualifies(text: str) -> bool:
    """`flavor` qualifies as a positive Triton-context term ONLY when
    the same review also contains a drink-category anchor."""
    low = (text or "").lower()
    if "flavor" not in low and "flavour" not in low:
        return False
    for anchor in (
        _STRONG_CATEGORY_ANCHORS
        + _MEDIUM_CATEGORY_ANCHORS
        + _WEAK_CATEGORY_ANCHORS
    ):
        if anchor in low:
            return True
    for brand in _NAMED_BRANDS_DRINK:
        if brand.lower() in low:
            return True
    # Prime-energy / -hydration counts:
    if any(p in low for p in _PRIME_DRINK_PHRASES):
        return True
    return False


# ---------------------------------------------------------------------------
# Confidence scorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewScoreDetail:
    """Structured scoring breakdown for one review (deterministic)."""
    confidence: ReviewConfidence
    score: int
    matched_terms: tuple[str, ...]
    denylist_hits: tuple[str, ...]
    metadata_category_hits: tuple[str, ...]
    product_title_hits: tuple[str, ...]
    review_text_hits: tuple[str, ...]
    prime_context: PrimeContext
    rejection_reason: str | None
    has_metadata: bool


def _hits_in(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    low = (text or "").lower()
    return tuple(t for t in terms if t.lower() in low)


def score_review(
    *,
    review: AmazonReviewRecord,
    metadata: AmazonProductMetadata | None,
) -> ReviewScoreDetail:
    """Score one (review, joined-metadata) candidate.

    Returns a `ReviewScoreDetail` with deterministic
    `confidence` ∈ {HIGH, MEDIUM, LOW, REJECTED}. Pure function,
    no I/O, no LLM."""
    review_blob = f"{review.title} {review.text}".strip()
    review_low = review_blob.lower()
    meta_blob = ""
    title_blob = ""
    if metadata is not None:
        title_blob = metadata.title or ""
        meta_blob = " | ".join(filter(None, (
            metadata.title,
            metadata.store,
            metadata.main_category,
            " | ".join(metadata.categories),
            metadata.description,
            " | ".join(metadata.features),
            " | ".join(f"{k}={v}" for k, v in metadata.details_summary.items()),
        )))
    meta_low = meta_blob.lower()

    # --- Component matches --------------------------------------------
    review_hits = (
        _hits_in(review_blob, _STRONG_CATEGORY_ANCHORS)
        + _hits_in(review_blob, _MEDIUM_CATEGORY_ANCHORS)
        + _hits_in(review_blob, _WEAK_CATEGORY_ANCHORS)
        + _hits_in(review_blob, _NAMED_BRANDS_DRINK)
    )
    metadata_category_hits: tuple[str, ...] = ()
    title_hits: tuple[str, ...] = ()
    if metadata is not None:
        # Cleaner: look at metadata.categories explicitly (joined string).
        cat_blob = " | ".join(metadata.categories)
        metadata_category_hits = (
            _hits_in(metadata.main_category + " | " + cat_blob,
                     _METADATA_CATEGORY_ANCHORS)
            + _hits_in(metadata.main_category + " | " + cat_blob,
                       _STRONG_CATEGORY_ANCHORS)
            + _hits_in(metadata.main_category + " | " + cat_blob,
                       _MEDIUM_CATEGORY_ANCHORS)
        )
        title_hits = (
            _hits_in(title_blob, _STRONG_CATEGORY_ANCHORS)
            + _hits_in(title_blob, _MEDIUM_CATEGORY_ANCHORS)
            + _hits_in(title_blob, _NAMED_BRANDS_DRINK)
        )

    # --- Prime context ------------------------------------------------
    full_blob = f"{review_blob} {meta_blob}"
    prime_ctx = prime_context_classification(full_blob)

    # --- Score build --------------------------------------------------
    score = 0
    matched_terms: list[str] = []
    denylist_hits: list[str] = []
    rejection_reason: str | None = None

    for t in _STRONG_CATEGORY_ANCHORS:
        if t in review_low or t in meta_low:
            score += 3
            matched_terms.append(t)
    for t in _MEDIUM_CATEGORY_ANCHORS:
        if t in review_low or t in meta_low:
            score += 2
            matched_terms.append(t)
    for t in _WEAK_CATEGORY_ANCHORS:
        if t in review_low or t in meta_low:
            score += 1
            matched_terms.append(t)
    for b in _NAMED_BRANDS_DRINK:
        if b.lower() in review_low or b.lower() in meta_low:
            score += 2
            matched_terms.append(b)

    # Prime: drink-context = +3, shipping-context = -3 (if no drink
    # context), ambiguous = 0.
    if prime_ctx is PrimeContext.DRINK:
        score += 3
        matched_terms.append("Prime (drink)")
    elif prime_ctx is PrimeContext.SHIPPING:
        score -= 3
        denylist_hits.append("Prime (shipping)")

    # Flavor: only counts if it qualifies (co-occurs with anchor).
    if flavor_qualifies(review_blob) or flavor_qualifies(meta_blob):
        score += 1
        matched_terms.append("flavor (qualified)")
    elif "flavor" in review_low or "flavour" in review_low:
        denylist_hits.append("flavor (unqualified)")

    # Metadata-side bonuses
    if metadata_category_hits:
        score += 2
        matched_terms.extend(f"meta:{h}" for h in metadata_category_hits)
    if title_hits:
        score += 2
        matched_terms.extend(f"title:{h}" for h in title_hits)

    # Sports_and_Outdoors-style metadata mismatch heuristic: if the
    # review's metadata main_category is generic sporting goods AND
    # metadata categories don't include any drink anchor AND no review
    # text drink anchor is present, downgrade.
    sports_equipment_hint = False
    if metadata is not None:
        cat_low = " | ".join(metadata.categories).lower()
        sports_equipment_hint = (
            "sports & outdoors" in cat_low
            or "outdoor recreation" in cat_low
            or "exercise & fitness" in cat_low
        )
    if (
        sports_equipment_hint
        and not metadata_category_hits
        and not any(a in review_low for a in _STRONG_CATEGORY_ANCHORS + _MEDIUM_CATEGORY_ANCHORS)
    ):
        score -= 2
        denylist_hits.append("metadata-mismatch:sports-equipment")

    # --- Rejection rules ----------------------------------------------
    has_any_signal = bool(matched_terms)
    if not has_any_signal:
        rejection_reason = "no_drink_category_anchor"
    elif (
        prime_ctx is PrimeContext.SHIPPING
        and not any(a in review_low or a in meta_low
                    for a in _STRONG_CATEGORY_ANCHORS + _MEDIUM_CATEGORY_ANCHORS)
        and not metadata_category_hits
        and not title_hits
    ):
        rejection_reason = "prime_shipping_only"
    elif len(matched_terms) == 1 and matched_terms[0] == "flavor (qualified)":
        # Flavor qualifies but is the *only* signal — mark as low,
        # not rejected.
        rejection_reason = None

    # --- Label --------------------------------------------------------
    if rejection_reason:
        confidence = ReviewConfidence.REJECTED
    elif score >= 6:
        confidence = ReviewConfidence.HIGH_CONFIDENCE
    elif score >= 3:
        confidence = ReviewConfidence.MEDIUM_CONFIDENCE
    elif score >= 1:
        confidence = ReviewConfidence.LOW_CONFIDENCE
    else:
        confidence = ReviewConfidence.REJECTED
        rejection_reason = rejection_reason or "score_below_threshold"

    return ReviewScoreDetail(
        confidence=confidence,
        score=score,
        matched_terms=tuple(matched_terms),
        denylist_hits=tuple(denylist_hits),
        metadata_category_hits=tuple(set(metadata_category_hits)),
        product_title_hits=tuple(set(title_hits)),
        review_text_hits=tuple(set(review_hits)),
        prime_context=prime_ctx,
        rejection_reason=rejection_reason,
        has_metadata=metadata is not None,
    )
