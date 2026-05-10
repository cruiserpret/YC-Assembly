"""Phase 8.5A — Amazon Reviews 2023 LOCAL dataset adapter.

Source: https://amazon-reviews-2023.github.io/ — McAuley Lab dataset
distributed as per-category JSONL (often gzipped) files. The adapter
NEVER calls Amazon's API and NEVER scrapes Amazon.com — both
forbidden, drift-tested.

Critical safety properties (drift-tested):

  * NO HTTP transport imported here. The adapter is local-only.
  * NO `requests` / `httpx` / `urllib` / `aiohttp` /
    `selenium` / `playwright` / `beautifulsoup4` / `bs4` /
    `scrapy` / `urllib3` imports anywhere in the package.
  * `user_id` from the raw record is NEVER stored verbatim. It
    is hashed to a stable opaque token (SHA-256, first 16 hex
    chars) — sufficient for cross-review dedup, insufficient
    for re-identification.
  * Image URLs (`images` field) are NEVER stored.
  * Reviews loaded here are CANDIDATE evidence — they MUST flow
    through the existing Phase 8.2x redaction + sensitive-filter
    + dedup discipline before any persona ever sees them.

The reader is STREAMING — never loads a full category file into
memory. The preflight script caps at 1000 records per category
during the 8.5A dry-run shape.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


_DEFAULT_PER_CATEGORY_CAP = 1000
_DEFAULT_MIN_TEXT_LENGTH = 30
_DEFAULT_MAX_TEXT_LENGTH = 4000

CATEGORIES_ALL = "ALL"
_AmazonCategoryListing = list[str] | Literal["ALL"]  # type: ignore[type-arg]


@dataclass(frozen=True)
class AmazonReviewsAdapterConfig:
    """Per-invocation reading caps."""
    max_records_per_category: int = _DEFAULT_PER_CATEGORY_CAP
    min_text_length: int = _DEFAULT_MIN_TEXT_LENGTH
    max_text_length: int = _DEFAULT_MAX_TEXT_LENGTH


@dataclass(frozen=True)
class AmazonReviewRecord:
    """Normalized Amazon review.

    `user_id_hash` is the SHA-256 first-16-hex of the raw user_id.
    `images` is intentionally absent — image URLs are never stored.
    """
    category: str
    parent_asin: str | None
    asin: str | None
    rating: float | None
    title: str
    text: str
    helpful_vote: int | None
    verified_purchase: bool | None
    timestamp: int | None
    user_id_hash: str | None


def resolve_categories(
    *,
    raw_setting: str | None,
    dataset_dir: Path,
) -> _AmazonCategoryListing:
    """Parse the `AMAZON_REVIEWS_2023_CATEGORIES` setting.

      * "ALL" (case-insensitive) — return the literal "ALL" sentinel
        so callers can detect future-proof "load every category"
        intent.
      * comma-separated list — return a trimmed non-empty list.
      * empty / None — return [].
    """
    s = (raw_setting or "").strip()
    if not s:
        return []
    if s.upper() == "ALL":
        return CATEGORIES_ALL
    return [c.strip() for c in s.split(",") if c.strip()]


def discover_category_files(
    *,
    dataset_dir: Path,
    categories: _AmazonCategoryListing,
) -> dict[str, list[Path]]:
    """Locate raw JSONL / JSONL.gz review files per category under
    `<dataset_dir>/raw/`.

    The McAuley Lab dataset's filename convention is e.g.
    `Grocery_and_Gourmet_Food.jsonl` (or `.jsonl.gz`). The adapter
    matches by category-name prefix so it tolerates either suffix
    plus optional sub-versions like `_review.jsonl.gz`.

    Returns a dict mapping category-name to a list of matching file
    paths. If `categories == "ALL"`, every category that has at
    least one matching file under `raw/` is included. Categories
    with no matching files appear as empty lists so callers can
    report missing files explicitly.
    """
    raw_dir = dataset_dir / "raw"
    if not raw_dir.is_dir():
        # Caller decides how to surface this — typically a clean
        # operator-facing error rather than a hard exception.
        return {}

    all_files = sorted(raw_dir.glob("*.jsonl*"))

    def _matches(file: Path, cat: str) -> bool:
        return file.name.lower().startswith(cat.lower())

    if categories == CATEGORIES_ALL:
        # Discover all categories from filenames. The category name
        # is the filename stem before the first ".jsonl"/".json"
        # suffix and before any trailing "_review" / "_reviews"
        # / "_meta" sub-version.
        discovered: dict[str, list[Path]] = {}
        for f in all_files:
            stem = f.name
            # Strip suffixes
            for suf in (".jsonl.gz", ".jsonl"):
                if stem.lower().endswith(suf):
                    stem = stem[: -len(suf)]
                    break
            # Strip sub-versions
            for sub in ("_reviews", "_review", "_meta"):
                if stem.lower().endswith(sub):
                    stem = stem[: -len(sub)]
                    break
            discovered.setdefault(stem, []).append(f)
        return discovered

    if not isinstance(categories, list):
        return {}
    return {cat: [f for f in all_files if _matches(f, cat)] for cat in categories}


def parse_amazon_review_line(
    *,
    raw_line: str,
    category: str,
) -> AmazonReviewRecord | None:
    """Parse one JSONL line from the McAuley Lab Amazon Reviews 2023
    schema into a normalized `AmazonReviewRecord`. Returns None on
    parse error (calling code logs as a skipped record)."""
    try:
        obj: dict[str, Any] = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    user_id_hash: str | None = None
    raw_uid = obj.get("user_id")
    if isinstance(raw_uid, str) and raw_uid:
        digest = hashlib.sha256(raw_uid.encode("utf-8")).hexdigest()
        user_id_hash = digest[:16]
    text = (obj.get("text") or "").strip()
    title = (obj.get("title") or "").strip()
    rating = obj.get("rating")
    try:
        rating_f = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating_f = None
    return AmazonReviewRecord(
        category=category,
        parent_asin=obj.get("parent_asin"),
        asin=obj.get("asin"),
        rating=rating_f,
        title=title,
        text=text,
        helpful_vote=obj.get("helpful_vote"),
        verified_purchase=obj.get("verified_purchase"),
        timestamp=obj.get("timestamp"),
        user_id_hash=user_id_hash,
    )


def looks_like_low_quality_review(
    *,
    record: AmazonReviewRecord,
    config: AmazonReviewsAdapterConfig,
) -> bool:
    """Reject reviews that are too short, too long, or
    template-shaped to add value to a market simulation.

    Conservative — only obvious noise. The downstream sensitive-
    filter + redaction pipeline does the deeper work."""
    text = record.text
    if not text:
        return True
    if len(text) < config.min_text_length:
        return True
    if len(text) > config.max_text_length:
        return True
    # All-caps spam
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 20:
        upper = sum(1 for c in letters if c.isupper())
        if upper / len(letters) >= 0.85:
            return True
    return False


def matches_search_terms(
    *,
    record: AmazonReviewRecord,
    search_terms: list[str],
) -> list[str]:
    """Return the subset of `search_terms` that appear (case-
    insensitive) in the review's title + text combined.

    Empty list means the review is off-brief and can be skipped."""
    if not search_terms:
        return []
    blob = f"{record.title} {record.text}".lower()
    matched: list[str] = []
    for term in search_terms:
        # Word-boundary match for short terms; substring for multi-
        # word phrases (because `\bpre-workout\b` doesn't match
        # `pre-workout` reliably across all regex flavors due to the
        # hyphen).
        t = term.strip().lower()
        if not t:
            continue
        if " " in t or "-" in t:
            if t in blob:
                matched.append(term)
        else:
            if re.search(rf"\b{re.escape(t)}\b", blob):
                matched.append(term)
    return matched


class AmazonReviewsLocalReader:
    """Streaming reader over local Amazon Reviews 2023 files.

    Construction is cheap — opens nothing. `iter_category` opens
    files lazily and yields one normalized record at a time.
    """

    def __init__(
        self,
        *,
        dataset_dir: Path,
        config: AmazonReviewsAdapterConfig | None = None,
    ) -> None:
        self._dir = dataset_dir
        self._config = config or AmazonReviewsAdapterConfig()

    @property
    def config(self) -> AmazonReviewsAdapterConfig:
        return self._config

    def iter_category(
        self,
        *,
        category: str,
        files: list[Path],
        max_records: int | None = None,
    ) -> Iterator[AmazonReviewRecord]:
        """Yield up to `max_records` normalized records from the given
        category's files.

        Streams (no full-file load). Skips parse failures silently.
        Drops low-quality reviews per `looks_like_low_quality_review`.
        """
        cap = max_records or self._config.max_records_per_category
        emitted = 0
        for path in files:
            if emitted >= cap:
                return
            opener = gzip.open if path.suffix == ".gz" else open
            try:
                with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[arg-type]
                    for raw in fh:
                        if emitted >= cap:
                            return
                        rec = parse_amazon_review_line(
                            raw_line=raw, category=category,
                        )
                        if rec is None:
                            continue
                        if looks_like_low_quality_review(
                            record=rec, config=self._config,
                        ):
                            continue
                        emitted += 1
                        yield rec
            except OSError:
                # File present but unreadable; skip without raising.
                continue
