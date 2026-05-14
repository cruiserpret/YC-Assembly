"""Phase 11A — AmazonReviewsProvider façade.

Higher-level interface on top of the Phase 8.5A local-dataset reader.
The provider never auto-loads — instantiation is cheap, but every
search method short-circuits to "empty" when
`ASSEMBLY_AMAZON_REVIEWS_ENABLED` is false. That keeps the rest of
the Assembly pipeline insulated from Phase 11A's existence while the
feature is still gated.

For Phase 11A the provider operates only on small local fixtures:

  * `search_by_product_name(name)` — finds reviews whose `title` or
    review `text` mentions the product name (case-insensitive).
  * `search_by_category(category)` — returns every accepted review
    in a known category, up to `max_items_per_run`.
  * `search_by_competitor(competitor)` — finds reviews whose
    distilled signals carry a matching `competitor_mention`.
  * `retrieve_review_signals(...)` — runs the distiller across the
    chosen subset and returns DistilledSignals (does NOT write to DB
    yet — Phase 11B introduces the persistence pass).

No DB writes happen here. Phase 11B will plug a thin persistence
layer on top.
"""
from __future__ import annotations

import gzip
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from assembly.sources.amazon_reviews_2023 import (
    AmazonReviewRecord,
    parse_amazon_review_line,
)
from assembly.sources.amazon_reviews_provider.distiller import (
    DistilledSignal,
    DistillerConfig,
    distill_review_signals,
    is_review_eligible,
)


class ProviderUnavailableError(RuntimeError):
    """Raised when caller explicitly asks the provider to do work
    while `ASSEMBLY_AMAZON_REVIEWS_ENABLED` is false. Search methods
    that get called *without* the feature flag on simply return empty
    lists and never raise — only callers that explicitly ask
    "I require the provider, fail loudly if not configured" hit this.
    """


@dataclass(frozen=True)
class AmazonReviewsProviderConfig:
    """Provider-level config. Maps 1:1 onto the `ASSEMBLY_AMAZON_REVIEWS_*`
    settings, but kept as a dataclass so the provider can be instantiated
    in tests without spinning up the full `Settings` object."""

    enabled: bool = False
    data_dir: Path | None = None
    categories: tuple[str, ...] = ()
    max_items_per_run: int = 200
    min_review_chars: int = 40

    @classmethod
    def from_settings(cls, settings: object) -> "AmazonReviewsProviderConfig":
        """Read fields off an `assembly.config.Settings` instance
        without importing it (so this module stays import-cheap)."""
        data_dir_raw = getattr(settings, "amazon_reviews_data_dir", None)
        categories_raw = getattr(
            settings, "amazon_reviews_categories_list", (),
        )
        return cls(
            enabled=bool(getattr(settings, "amazon_reviews_enabled", False)),
            data_dir=Path(data_dir_raw) if data_dir_raw else None,
            categories=tuple(categories_raw) if categories_raw else (),
            max_items_per_run=int(
                getattr(settings, "amazon_reviews_max_items_per_run", 200),
            ),
            min_review_chars=int(
                getattr(settings, "amazon_reviews_min_review_chars", 40),
            ),
        )


@dataclass
class _ProductMetadata:
    """Lightweight metadata index keyed by asin / parent_asin.

    Phase 8.5B's `MetadataIndex` does more sophisticated joining; the
    Phase 11A provider only needs (title, brand) per asin so it can
    attribute distilled signals to a product title.
    """

    by_asin: dict[str, tuple[str | None, str | None]] = field(
        default_factory=dict,
    )

    def lookup(
        self, asin: str | None, parent_asin: str | None,
    ) -> tuple[str | None, str | None]:
        for key in (asin, parent_asin):
            if key and key in self.by_asin:
                return self.by_asin[key]
        return (None, None)


def _open_text(path: Path) -> Iterator[str]:
    """Open a `.jsonl` or `.jsonl.gz` file as a stream of decoded
    lines. Local-only; no HTTP, no network."""
    if path.suffix == ".gz":
        with gzip.open(path, mode="rt", encoding="utf-8") as fh:
            for line in fh:
                yield line
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            yield line


def _read_metadata(path: Path) -> _ProductMetadata:
    """Read a McAuley-Lab-style `*_meta.jsonl[.gz]` file into a tiny
    `(asin -> (title, brand))` lookup. Tolerates missing fields."""
    idx = _ProductMetadata()
    if not path.exists():
        return idx
    import json
    for raw in _open_text(path):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        asin = obj.get("parent_asin") or obj.get("asin")
        if not isinstance(asin, str) or not asin:
            continue
        title = obj.get("title")
        brand = obj.get("brand") or obj.get("store")
        idx.by_asin[asin] = (
            (title.strip() if isinstance(title, str) else None),
            (brand.strip() if isinstance(brand, str) else None),
        )
        # Also map the secondary id so both lookups hit.
        for k in ("asin", "parent_asin"):
            v = obj.get(k)
            if isinstance(v, str) and v and v != asin:
                idx.by_asin.setdefault(v, idx.by_asin[asin])
    return idx


def _category_review_path(
    data_dir: Path,
    category: str,
) -> Path | None:
    """Find the first review file matching `category` under the
    dataset's `raw/` directory, honoring the McAuley Lab filename
    conventions (`Category.jsonl[.gz]`, `Category_reviews.jsonl[.gz]`,
    etc.). Returns None if nothing matches.
    """
    raw_dir = data_dir / "raw"
    if not raw_dir.is_dir():
        return None
    candidates = sorted(raw_dir.glob(f"{category}*.jsonl*"))
    for c in candidates:
        # Skip metadata files.
        stem = c.name.lower()
        if "_meta" in stem:
            continue
        return c
    return None


def _category_metadata_path(
    data_dir: Path,
    category: str,
) -> Path | None:
    raw_dir = data_dir / "raw"
    if not raw_dir.is_dir():
        return None
    candidates = sorted(raw_dir.glob(f"{category}*_meta.jsonl*"))
    return candidates[0] if candidates else None


class AmazonReviewsProvider:
    """Phase 11A scaffold provider.

    Instantiation is always safe — the constructor accepts any config
    and never touches disk. The provider only touches disk inside the
    `search_*` / `retrieve_review_signals` methods, and only when the
    feature flag is on.
    """

    def __init__(
        self,
        config: AmazonReviewsProviderConfig | None = None,
    ) -> None:
        self.config = config or AmazonReviewsProviderConfig()
        self._distiller_config = DistillerConfig(
            min_review_chars=self.config.min_review_chars,
        )

    # -- helpers -----------------------------------------------------

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled and self.config.data_dir is not None

    def _resolved_categories(self) -> Sequence[str]:
        if self.config.categories:
            return self.config.categories
        # Fall back to whatever categories have review files on disk.
        if self.config.data_dir is None:
            return ()
        raw_dir = self.config.data_dir / "raw"
        if not raw_dir.is_dir():
            return ()
        seen: list[str] = []
        for f in sorted(raw_dir.glob("*.jsonl*")):
            stem = f.name
            for suf in (".jsonl.gz", ".jsonl"):
                if stem.lower().endswith(suf):
                    stem = stem[: -len(suf)]
                    break
            for sub in ("_reviews", "_review", "_meta"):
                if stem.lower().endswith(sub):
                    stem = stem[: -len(sub)]
                    break
            if stem not in seen:
                seen.append(stem)
        return tuple(seen)

    def _iter_category(
        self,
        category: str,
    ) -> Iterator[tuple[AmazonReviewRecord, str | None, str | None]]:
        """Stream review rows for one category, joined with metadata
        when available. Stops at `max_items_per_run` *accepted* rows."""
        if self.config.data_dir is None:
            return
        review_path = _category_review_path(self.config.data_dir, category)
        if review_path is None:
            return
        meta_path = _category_metadata_path(self.config.data_dir, category)
        meta_idx = _read_metadata(meta_path) if meta_path else _ProductMetadata()

        accepted = 0
        cap = self.config.max_items_per_run
        for raw_line in _open_text(review_path):
            record = parse_amazon_review_line(
                raw_line=raw_line, category=category,
            )
            if record is None:
                continue
            ok, _reason = is_review_eligible(record, self._distiller_config)
            if not ok:
                continue
            title, brand = meta_idx.lookup(
                record.asin, record.parent_asin,
            )
            yield record, title, brand
            accepted += 1
            if accepted >= cap:
                return

    # -- public API --------------------------------------------------

    def search_by_product_name(
        self,
        product_name: str,
        *,
        require_enabled: bool = False,
    ) -> list[AmazonReviewRecord]:
        """Return raw reviews whose title or text contains
        `product_name` (case-insensitive). Empty list when the
        provider is disabled, unless `require_enabled=True`."""
        if not product_name.strip():
            return []
        if not self.is_enabled:
            if require_enabled:
                raise ProviderUnavailableError(
                    "ASSEMBLY_AMAZON_REVIEWS_ENABLED is false; provider "
                    "cannot service this call.",
                )
            return []
        needle = product_name.strip().lower()
        out: list[AmazonReviewRecord] = []
        for category in self._resolved_categories():
            for record, _title, _brand in self._iter_category(category):
                hay = f"{record.title}\n{record.text}".lower()
                if needle in hay:
                    out.append(record)
                    if len(out) >= self.config.max_items_per_run:
                        return out
        return out

    def search_by_category(
        self,
        category: str,
        *,
        require_enabled: bool = False,
    ) -> list[AmazonReviewRecord]:
        if not category.strip():
            return []
        if not self.is_enabled:
            if require_enabled:
                raise ProviderUnavailableError(
                    "ASSEMBLY_AMAZON_REVIEWS_ENABLED is false.",
                )
            return []
        out: list[AmazonReviewRecord] = []
        for record, _title, _brand in self._iter_category(category):
            out.append(record)
        return out

    def search_by_competitor(
        self,
        competitor: str,
        *,
        require_enabled: bool = False,
    ) -> list[DistilledSignal]:
        """Return *distilled signals* (not raw reviews) whose
        `competitor_mention` matches. Returning distilled signals
        here is intentional — finding "who switched from X" is
        meaningful only after the distiller has identified the switch
        verb + capture group.
        """
        if not competitor.strip():
            return []
        if not self.is_enabled:
            if require_enabled:
                raise ProviderUnavailableError(
                    "ASSEMBLY_AMAZON_REVIEWS_ENABLED is false.",
                )
            return []
        needle = competitor.strip().lower()
        signals = self.retrieve_review_signals()
        return [
            s for s in signals
            if s.competitor_mention
            and needle in s.competitor_mention.lower()
        ]

    def retrieve_review_signals(
        self,
        *,
        categories: Sequence[str] | None = None,
        require_enabled: bool = False,
    ) -> list[DistilledSignal]:
        """Distill every accepted review in the configured (or
        passed-in) categories. Caller controls subset size via
        `max_items_per_run` from config."""
        if not self.is_enabled:
            if require_enabled:
                raise ProviderUnavailableError(
                    "ASSEMBLY_AMAZON_REVIEWS_ENABLED is false.",
                )
            return []
        target_categories = (
            tuple(categories) if categories is not None
            else tuple(self._resolved_categories())
        )
        out: list[DistilledSignal] = []
        for category in target_categories:
            for record, title, brand in self._iter_category(category):
                signals = distill_review_signals(
                    record,
                    config=self._distiller_config,
                    product_title=title,
                    brand=brand,
                )
                out.extend(signals)
        return out
