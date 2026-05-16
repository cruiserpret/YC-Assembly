"""Phase 11C.1 — runtime retrieval of distilled Amazon review signals.

This module exposes a feature-flagged retrieval layer that reads
already-distilled signals out of the `amazon_review_signal` table and
returns a sanitized evidence package the simulation pipeline can
optionally feed into persona generation.

Key invariants enforced here (drift-tested):

  * The retriever returns an EMPTY evidence package unless BOTH
    `ASSEMBLY_AMAZON_REVIEWS_ENABLED=true` AND
    `ASSEMBLY_AMAZON_REVIEWS_RUNTIME_ENABLED=true`. The double-gate
    means a Phase-11B ingestion flag flip alone does not enable
    live retrieval.
  * The exposed `RetrievedSignal` dataclass omits `source_review_hash`,
    `user_id` (the row never had one), images (the row never had
    any), `created_at`, and the row's internal id. Persona-grade
    output only.
  * Per-brand, per-theme, per-category, per-competitor, and
    per-run caps bound how much Amazon evidence any one simulation
    can consume — Amazon must not crowd out Brave / Tavily /
    YouTube.

The retriever is offline-safe: zero HTTP imports, zero scraping
verbs, only reads the local Postgres table that Phase 11B already
populated.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from assembly.sources.amazon_reviews_provider.signal_types import (
    SIGNAL_TYPES,
    SentimentBucket,
    SignalType,
)


# ---------------------------------------------------------------------------
# Config + brief shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalConfig:
    """Knobs for the retriever. Wraps the Phase-11C settings without
    importing them directly so the retriever can be unit-tested
    without a full `Settings` object."""

    enabled: bool = False
    runtime_enabled: bool = False
    max_signals_per_run: int = 80
    max_signals_per_category: int = 40
    max_signals_per_competitor: int = 20
    max_signals_per_brand: int = 8
    max_signals_per_theme: int = 10

    @classmethod
    def from_settings(cls, settings: object) -> "RetrievalConfig":
        return cls(
            enabled=bool(getattr(settings, "amazon_reviews_enabled", False)),
            runtime_enabled=bool(
                getattr(settings, "amazon_reviews_runtime_enabled", False),
            ),
            max_signals_per_run=int(
                getattr(settings, "amazon_reviews_max_signals_per_run", 80),
            ),
            max_signals_per_category=int(
                getattr(
                    settings, "amazon_reviews_max_signals_per_category", 40,
                ),
            ),
            max_signals_per_competitor=int(
                getattr(
                    settings,
                    "amazon_reviews_max_signals_per_competitor",
                    20,
                ),
            ),
            max_signals_per_brand=int(
                getattr(settings, "amazon_reviews_max_signals_per_brand", 8),
            ),
            max_signals_per_theme=int(
                getattr(settings, "amazon_reviews_max_signals_per_theme", 10),
            ),
        )

    @property
    def fully_enabled(self) -> bool:
        """Both gates must be on for the retriever to do real work."""
        return self.enabled and self.runtime_enabled


@dataclass(frozen=True)
class ProductBriefShape:
    """The narrow slice of a founder's product brief that the
    retriever cares about. Decoupled from any larger
    `SimulationBriefIn` / `FounderBriefIn` schema so the retriever
    stays usable in tests without spinning up Pydantic."""

    product_name: str
    description: str = ""
    category_hint: str | None = None
    competitors: Sequence[str] = ()


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievedSignal:
    """The persona-grade shape of one Amazon evidence row.

    Deliberately omits every column the persona layer must never see:
      * no `source_review_hash` (implementation detail for dedup only)
      * no `user_id` / `user_id_hash` (the DB never persisted one)
      * no `images` / `image_url` (the DB never persisted any)
      * no `created_at` / row id (DB plumbing)
    """

    signal_type: SignalType
    sentiment_bucket: SentimentBucket
    theme: str | None
    category: str
    brand: str | None
    product_title: str | None
    rating: int | None
    short_snippet: str
    competitor_mention: str | None
    use_case: str | None
    verified_purchase: bool | None
    helpful_votes: int | None


@dataclass
class AmazonEvidencePackage:
    """What the retriever returns to the simulation pipeline."""

    attempted: bool = False
    feature_flag_status: dict[str, bool] = field(default_factory=dict)
    category_matched: str | None = None
    signals: list[RetrievedSignal] = field(default_factory=list)
    distribution: dict[str, int] = field(default_factory=dict)
    brand_coverage: float = 0.0
    title_coverage: float = 0.0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Brief → category classifier (transparent, keyword-based)
# ---------------------------------------------------------------------------


# Pilot-category keyword map. Each (category, keywords) pair anchors
# the classifier in a specific McAuley category. Keep this product-
# agnostic — these are CATEGORY keywords, not product-name strings.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Health_and_Personal_Care",
        (
            "supplement", "vitamin", "wellness", "health", "fitness",
            "therapy", "medical", "wearable", "tracker", "skincare",
            "personal care", "hygiene", "stress", "sleep",
        ),
    ),
    (
        "All_Beauty",
        (
            "beauty", "cosmetic", "makeup", "skincare", "lotion",
            "shampoo", "fragrance", "perfume", "nail", "lipstick",
            "hair", "serum", "moisturizer",
        ),
    ),
    (
        "Software",
        (
            "software", "app", "saas", "subscription service",
            "antivirus", "license", "cloud", "platform",
            "browser extension", "chrome extension", "windows app",
            "mac app", "ios app", "android app",
        ),
    ),
    (
        "Industrial_and_Scientific",
        (
            "industrial", "scientific", "laboratory", "lab equipment",
            "instrument", "sensor", "tool", "machinery", "compressor",
            "calibration", "measurement", "ppe",
        ),
    ),
    (
        "Home_and_Kitchen",
        (
            "kitchen", "cookware", "appliance", "blender", "oven",
            "coffee maker", "vacuum", "home goods", "dishwasher",
        ),
    ),
    (
        "Electronics",
        (
            "headphones", "earbuds", "speaker", "smart home",
            "router", "camera", "tablet", "laptop", "phone",
            "wearable", "battery pack", "charger",
        ),
    ),
    (
        "Subscription_Boxes",
        (
            "subscription box", "monthly box", "curated box",
            "mystery box",
        ),
    ),
)


def classify_brief_to_category(brief: ProductBriefShape) -> str | None:
    """Map a product brief to an Amazon category name.

    Strategy (in order):
      1. If the brief has an explicit `category_hint` that matches a
         known pilot category (case-insensitive), use it.
      2. Otherwise count keyword hits across all pilot categories.
         The category with the most hits wins.
      3. If nothing matches, return None — the retriever will report
         `category_matched=None` and only fall back to brand /
         competitor lookups (which don't need a category).
    """
    hint = (brief.category_hint or "").strip()
    if hint:
        for category, _kws in _CATEGORY_KEYWORDS:
            if hint.lower() == category.lower():
                return category

    blob = " ".join([
        brief.product_name or "",
        brief.description or "",
        brief.category_hint or "",
    ]).lower()
    if not blob.strip():
        return None

    scores: dict[str, int] = {}
    for category, kws in _CATEGORY_KEYWORDS:
        n = sum(1 for kw in kws if kw in blob)
        if n:
            scores[category] = n
    if not scores:
        return None
    # Highest score wins; ties broken by the order in
    # _CATEGORY_KEYWORDS (so Health_and_Personal_Care beats
    # All_Beauty when a brief mentions skincare *and* wellness).
    best = max(scores, key=lambda c: (scores[c], -_category_rank(c)))
    return best


def _category_rank(category: str) -> int:
    for i, (c, _) in enumerate(_CATEGORY_KEYWORDS):
        if c == category:
            return i
    return 9999


# ---------------------------------------------------------------------------
# SignalSource protocol — tested in-memory; production hits Postgres
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalRow:
    """A row from `amazon_review_signal` plus the
    derived/persisted fields the retriever needs for ranking.
    Mirrors the model's column shape so production + test paths
    share the same dataclass.
    """

    signal_type: SignalType
    sentiment_bucket: SentimentBucket
    theme: str | None
    category: str
    brand: str | None
    product_title: str | None
    asin: str | None
    parent_asin: str | None
    rating: int | None
    verified_purchase: bool | None
    helpful_votes: int | None
    short_snippet: str
    competitor_mention: str | None
    use_case: str | None
    source_review_hash: str


class SignalSource(Protocol):
    """Decoupled data-access layer. Production implementation uses
    SQLAlchemy; tests use an in-memory list."""

    async def fetch_by_category(
        self, category: str, *, limit: int,
    ) -> list[SignalRow]:  # pragma: no cover - protocol
        ...

    async def fetch_by_competitor(
        self, competitor: str, *, limit: int,
    ) -> list[SignalRow]:  # pragma: no cover - protocol
        ...

    async def fetch_by_theme(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int,
    ) -> list[SignalRow]:  # pragma: no cover - protocol
        ...


class InMemorySignalSource:
    """Test-only signal source. Takes a static list of rows."""

    def __init__(self, rows: Iterable[SignalRow]) -> None:
        self.rows: list[SignalRow] = list(rows)

    async def fetch_by_category(
        self, category: str, *, limit: int,
    ) -> list[SignalRow]:
        out = [r for r in self.rows if r.category == category]
        return _rank_signals(out)[:limit]

    async def fetch_by_competitor(
        self, competitor: str, *, limit: int,
    ) -> list[SignalRow]:
        needle = competitor.strip().lower()
        out = [
            r for r in self.rows
            if (r.competitor_mention or "").lower() == needle
            or (r.brand or "").lower() == needle
        ]
        return _rank_signals(out)[:limit]

    async def fetch_by_theme(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int,
    ) -> list[SignalRow]:
        wanted = set(signal_types)
        out = [r for r in self.rows if r.signal_type in wanted]
        return _rank_signals(out)[:limit]


def _rank_signals(rows: list[SignalRow]) -> list[SignalRow]:
    """Stable ranking: verified purchases first, then helpful_votes
    desc, then negative-rating-first for objection-like buckets."""

    def key(r: SignalRow) -> tuple[int, int, int, str]:
        # Sort key — lower tuple wins (we sort ascending).
        verified_rank = 0 if r.verified_purchase else 1
        helpful_rank = -(r.helpful_votes or 0)
        # For negatives we prefer LOW ratings (more objection signal).
        # For positives we prefer HIGH ratings.
        if r.signal_type in {
            "objection", "return_reason", "durability", "price",
            "setup", "support", "safety", "trust",
        } and (r.rating is not None) and r.rating <= 2:
            specificity_rank = -1
        elif r.signal_type in {"praise", "use_case", "proof_need"} \
                and (r.rating is not None) and r.rating >= 4:
            specificity_rank = -1
        else:
            specificity_rank = 0
        return (verified_rank, specificity_rank, helpful_rank,
                r.source_review_hash)

    return sorted(rows, key=key)


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


# Snippet collision dedup — fuzzy match on the first N chars after
# normalization. Two reviews that copy each other (Amazon often has
# template-generated reviews) hash identically and dedup-collapse.
_SNIPPET_DEDUP_PREFIX = 96


def _snippet_dedup_key(snippet: str) -> str:
    return " ".join(snippet.lower().split())[:_SNIPPET_DEDUP_PREFIX]


# Bucket map: which signal types we always try to pull from for a
# product brief, in priority order. The retriever asks for `n` of
# each, then balances down to the per-run cap.
_DEFAULT_BUCKET_PRIORITY: tuple[SignalType, ...] = (
    "objection",
    "price",
    "durability",
    "trust",
    "setup",
    "support",
    "safety",
    "switch_reason",
    "return_reason",
    "praise",
    "use_case",
    "proof_need",
)


class AmazonSignalRetriever:
    """Phase 11C.1 runtime retriever.

    Instantiation is always safe. Every public method short-circuits
    to an empty result when the feature flags are off.
    """

    def __init__(
        self,
        source: SignalSource,
        *,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._source = source
        self.config = config or RetrievalConfig()

    @property
    def is_active(self) -> bool:
        return self.config.fully_enabled

    # -- public retrieval methods ------------------------------------

    async def retrieve_by_category(
        self,
        category: str,
        *,
        limit: int | None = None,
    ) -> list[RetrievedSignal]:
        if not self.is_active or not category.strip():
            return []
        rows = await self._source.fetch_by_category(
            category,
            limit=(limit or self.config.max_signals_per_category),
        )
        deduped = _dedup_rows(rows)
        capped = _apply_brand_theme_caps(
            deduped,
            max_per_brand=self.config.max_signals_per_brand,
            max_per_theme=self.config.max_signals_per_theme,
        )
        return [_to_retrieved(r) for r in capped]

    async def retrieve_by_brand_or_competitor(
        self,
        competitors: Sequence[str],
        *,
        limit: int | None = None,
    ) -> list[RetrievedSignal]:
        if not self.is_active or not competitors:
            return []
        per_competitor_cap = (
            limit or self.config.max_signals_per_competitor
        )
        merged: list[SignalRow] = []
        for c in competitors:
            if not c or not c.strip():
                continue
            merged.extend(
                await self._source.fetch_by_competitor(
                    c, limit=per_competitor_cap,
                ),
            )
        deduped = _dedup_rows(merged)
        capped = _apply_brand_theme_caps(
            deduped,
            max_per_brand=self.config.max_signals_per_brand,
            max_per_theme=self.config.max_signals_per_theme,
        )
        return [_to_retrieved(r) for r in capped]

    async def retrieve_by_theme(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int | None = None,
    ) -> list[RetrievedSignal]:
        if not self.is_active or not signal_types:
            return []
        rows = await self._source.fetch_by_theme(
            signal_types,
            limit=(limit or self.config.max_signals_per_run),
        )
        deduped = _dedup_rows(rows)
        capped = _apply_brand_theme_caps(
            deduped,
            max_per_brand=self.config.max_signals_per_brand,
            max_per_theme=self.config.max_signals_per_theme,
        )
        return [_to_retrieved(r) for r in capped]

    async def retrieve_for_product_brief(
        self,
        brief: ProductBriefShape,
    ) -> AmazonEvidencePackage:
        """Full pipeline: classify category, mix signals across
        signal-type buckets, apply caps, package output."""

        pkg = AmazonEvidencePackage(
            feature_flag_status={
                "amazon_reviews_enabled": self.config.enabled,
                "amazon_reviews_runtime_enabled":
                    self.config.runtime_enabled,
            },
        )
        if not self.is_active:
            pkg.notes.append(
                "feature_flag_off — skipping all retrieval calls",
            )
            return pkg

        pkg.attempted = True
        category = classify_brief_to_category(brief)
        pkg.category_matched = category

        skipped: dict[str, int] = defaultdict(int)
        collected: list[SignalRow] = []

        # 1. Category-anchored pull, biased toward the
        # negative/discovery buckets first, then the positive buckets.
        if category:
            cat_rows = await self._source.fetch_by_category(
                category,
                limit=self.config.max_signals_per_category,
            )
            collected.extend(cat_rows)

        # 2. Competitor / brand lookups.
        for c in brief.competitors:
            if not c.strip():
                continue
            comp_rows = await self._source.fetch_by_competitor(
                c, limit=self.config.max_signals_per_competitor,
            )
            collected.extend(comp_rows)

        # 3. Theme lookups for high-priority signal types regardless
        # of category, so a brief whose category mis-classifies still
        # gets a representative slice.
        theme_rows = await self._source.fetch_by_theme(
            _DEFAULT_BUCKET_PRIORITY,
            limit=self.config.max_signals_per_run,
        )
        collected.extend(theme_rows)

        # ----- dedup + cap -----
        before = len(collected)
        deduped = _dedup_rows(collected, skipped_counter=skipped)
        skipped["pre_cap_pool_size"] = before

        balanced = _balance_buckets(
            deduped,
            max_per_run=self.config.max_signals_per_run,
            max_per_brand=self.config.max_signals_per_brand,
            max_per_theme=self.config.max_signals_per_theme,
            skipped_counter=skipped,
        )

        pkg.signals = [_to_retrieved(r) for r in balanced]
        pkg.distribution = _distribution(balanced)
        pkg.skipped_reasons = dict(skipped)
        if balanced:
            pkg.brand_coverage = sum(
                1 for r in balanced if r.brand
            ) / len(balanced)
            pkg.title_coverage = sum(
                1 for r in balanced if r.product_title
            ) / len(balanced)
        return pkg


# ---------------------------------------------------------------------------
# Helpers (pure functions, no state, no I/O)
# ---------------------------------------------------------------------------


def _dedup_rows(
    rows: list[SignalRow],
    *,
    skipped_counter: dict[str, int] | None = None,
) -> list[SignalRow]:
    """Drop duplicates by (source_review_hash, signal_type) and by
    fuzzy snippet collision. The first-seen row wins."""
    seen_keys: set[tuple[str, str]] = set()
    seen_snippets: set[tuple[str, str]] = set()
    out: list[SignalRow] = []
    for r in rows:
        key = (r.source_review_hash, r.signal_type)
        if key in seen_keys:
            if skipped_counter is not None:
                skipped_counter["dedup_hash_collision"] += 1
            continue
        snip_key = (r.signal_type, _snippet_dedup_key(r.short_snippet))
        if snip_key in seen_snippets:
            if skipped_counter is not None:
                skipped_counter["dedup_snippet_collision"] += 1
            continue
        seen_keys.add(key)
        seen_snippets.add(snip_key)
        out.append(r)
    return out


def _apply_brand_theme_caps(
    rows: list[SignalRow],
    *,
    max_per_brand: int,
    max_per_theme: int,
    skipped_counter: dict[str, int] | None = None,
) -> list[SignalRow]:
    """First-fit cap: walk the (already ranked) list and skip rows
    once their (brand, theme) bucket is full."""
    by_brand: dict[str | None, int] = defaultdict(int)
    by_theme: dict[str | None, int] = defaultdict(int)
    out: list[SignalRow] = []
    for r in rows:
        if by_brand[r.brand] >= max_per_brand:
            if skipped_counter is not None:
                skipped_counter["brand_cap"] += 1
            continue
        if by_theme[r.theme] >= max_per_theme:
            if skipped_counter is not None:
                skipped_counter["theme_cap"] += 1
            continue
        by_brand[r.brand] += 1
        by_theme[r.theme] += 1
        out.append(r)
    return out


def _balance_buckets(
    rows: list[SignalRow],
    *,
    max_per_run: int,
    max_per_brand: int,
    max_per_theme: int,
    skipped_counter: dict[str, int] | None = None,
) -> list[SignalRow]:
    """Round-robin across signal_type buckets so no single type
    dominates the final per-run pool. Then apply brand/theme caps."""
    by_type: dict[str, list[SignalRow]] = defaultdict(list)
    for r in rows:
        by_type[r.signal_type].append(r)
    # Re-rank within each bucket.
    for k in by_type:
        by_type[k] = _rank_signals(by_type[k])

    # Round-robin pull.
    ordered_types = [t for t in _DEFAULT_BUCKET_PRIORITY if t in by_type]
    # Append any types we didn't list explicitly (future-proof).
    for t in by_type:
        if t not in ordered_types:
            ordered_types.append(t)

    out: list[SignalRow] = []
    while len(out) < max_per_run and any(by_type.values()):
        progress = False
        for t in ordered_types:
            if len(out) >= max_per_run:
                break
            if by_type[t]:
                out.append(by_type[t].pop(0))
                progress = True
        if not progress:
            break

    # Now enforce brand + theme caps on the balanced pool.
    return _apply_brand_theme_caps(
        out,
        max_per_brand=max_per_brand,
        max_per_theme=max_per_theme,
        skipped_counter=skipped_counter,
    )


def _distribution(rows: list[SignalRow]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        out[r.signal_type] += 1
    return dict(out)


def _to_retrieved(r: SignalRow) -> RetrievedSignal:
    """Strip every field that must not reach the persona layer."""
    return RetrievedSignal(
        signal_type=r.signal_type,
        sentiment_bucket=r.sentiment_bucket,
        theme=r.theme,
        category=r.category,
        brand=r.brand,
        product_title=r.product_title,
        rating=r.rating,
        short_snippet=r.short_snippet,
        competitor_mention=r.competitor_mention,
        use_case=r.use_case,
        verified_purchase=r.verified_purchase,
        helpful_votes=r.helpful_votes,
    )


__all__ = [
    "AmazonEvidencePackage",
    "AmazonSignalRetriever",
    "InMemorySignalSource",
    "ProductBriefShape",
    "RetrievalConfig",
    "RetrievedSignal",
    "SIGNAL_TYPES",
    "SignalRow",
    "SignalSource",
    "classify_brief_to_category",
]
