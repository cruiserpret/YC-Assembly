"""Phase 11B — pilot-category ingestion engine.

This module owns the per-category ingestion loop:

  * iterate one category's review file via the Phase 11A reader,
  * distill each accepted row into `DistilledSignal`s,
  * de-duplicate by `(source_review_hash, signal_type)` against
    a persister-provided "already seen" set,
  * hand the accepted batch to a `SignalPersister` for write
    (dry-run mode plugs in a `NullSignalPersister` that records but
    never writes),
  * tally `IngestionStats` for the audit JSON.

The dedup + tally logic is intentionally separate from the DB so it
can be unit-tested without Postgres.

Safety carry-forwards from Phase 11A:
  * the distiller never writes the full raw review body
  * the distiller never persists `user_id` (only the SHA hash of it
    via the Phase 8.5A parser)
  * the distiller has no image-URL field at all
The persister sees ONLY `DistilledSignal` instances, so it can never
land any of those forbidden fields in the DB.
"""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from assembly.sources.amazon_reviews_2023 import AmazonReviewRecord
from assembly.sources.amazon_reviews_provider.distiller import (
    DistilledSignal,
    DistillerConfig,
    distill_review_signals,
    is_review_eligible,
)


# ---------------------------------------------------------------------------
# Stats + audit
# ---------------------------------------------------------------------------


@dataclass
class IngestionStats:
    """Tally for one category's ingestion run. Used to build the
    audit JSON the operator inspects after each run."""

    category: str
    input_file: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    rows_scanned: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_already_ingested: int = 0  # resume mode: hash already in DB

    rejection_reasons: Counter[str] = field(default_factory=Counter)

    signals_generated: int = 0
    signals_inserted: int = 0
    signals_skipped_duplicate: int = 0

    signal_type_distribution: Counter[str] = field(default_factory=Counter)
    sentiment_distribution: Counter[str] = field(default_factory=Counter)
    theme_distribution: Counter[str] = field(default_factory=Counter)

    sample_accepted_signals: list[DistilledSignal] = field(
        default_factory=list,
    )
    sample_rejected_rows: list[dict[str, str | int | None]] = field(
        default_factory=list,
    )

    dry_run: bool = True

    def record_rejection(
        self, record: AmazonReviewRecord, reason: str,
    ) -> None:
        self.rows_rejected += 1
        self.rejection_reasons[reason] += 1
        if len(self.sample_rejected_rows) < 5:
            self.sample_rejected_rows.append(
                {
                    "category": record.category,
                    "asin": record.asin or record.parent_asin,
                    "rating": int(record.rating)
                    if record.rating is not None else None,
                    "reason": reason,
                    "first_chars": (record.text or "")[:80],
                },
            )

    def record_accepted(self, signals: list[DistilledSignal]) -> None:
        self.rows_accepted += 1
        self.signals_generated += len(signals)
        for s in signals:
            self.signal_type_distribution[s.signal_type] += 1
            self.sentiment_distribution[s.sentiment_bucket] += 1
            if s.theme:
                self.theme_distribution[s.theme] += 1
            if len(self.sample_accepted_signals) < 8:
                self.sample_accepted_signals.append(s)

    def record_inserted(self, n: int) -> None:
        self.signals_inserted += n

    def record_dup_skip(self, n: int = 1) -> None:
        self.signals_skipped_duplicate += n

    @property
    def runtime_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return round(end - self.started_at, 3)

    @property
    def top_themes(self) -> list[tuple[str, int]]:
        return self.theme_distribution.most_common(10)


def build_audit_payload(stats: IngestionStats) -> dict[str, object]:
    """Serialize one category's `IngestionStats` into the audit-JSON
    shape the operator asked for in Phase 11B section 5."""

    return {
        "phase": "11B",
        "category": stats.category,
        "input_file": stats.input_file,
        "dry_run": stats.dry_run,
        "started_at": stats.started_at,
        "finished_at": stats.finished_at,
        "runtime_seconds": stats.runtime_seconds,
        "counts": {
            "rows_scanned": stats.rows_scanned,
            "rows_accepted": stats.rows_accepted,
            "rows_rejected": stats.rows_rejected,
            "rows_already_ingested": stats.rows_already_ingested,
            "signals_generated": stats.signals_generated,
            "signals_inserted": stats.signals_inserted,
            "signals_skipped_duplicate": stats.signals_skipped_duplicate,
        },
        "rejection_reasons": dict(stats.rejection_reasons),
        "signal_type_distribution": dict(stats.signal_type_distribution),
        "sentiment_distribution": dict(stats.sentiment_distribution),
        "top_themes": stats.top_themes,
        "sample_accepted_signals": [
            {
                "signal_type": s.signal_type,
                "sentiment_bucket": s.sentiment_bucket,
                "theme": s.theme,
                "category": s.category,
                "product_title": s.product_title,
                "brand": s.brand,
                "asin": s.asin,
                "parent_asin": s.parent_asin,
                "rating": s.rating,
                "short_snippet": s.short_snippet,
                "competitor_mention": s.competitor_mention,
                "use_case": s.use_case,
                "source_review_hash": s.source_review_hash,
            }
            for s in stats.sample_accepted_signals
        ],
        "sample_rejected_rows": stats.sample_rejected_rows,
    }


# ---------------------------------------------------------------------------
# Persister protocol
# ---------------------------------------------------------------------------


class SignalPersister(Protocol):
    """Persister abstraction so the ingestion loop can be tested
    without Postgres. Three async methods:

      * existing_review_hashes_for_category — used by --resume to
        skip reviews already turned into signals,
      * existing_signal_keys_for_category — used for de-dup so a
        rerun doesn't double-insert the same (hash, signal_type),
      * insert_signals — append a list of `DistilledSignal` rows
        and return the number actually persisted.
    """

    async def existing_review_hashes_for_category(
        self, category: str,
    ) -> set[str]:  # pragma: no cover - protocol
        ...

    async def existing_signal_keys_for_category(
        self, category: str,
    ) -> set[tuple[str, str]]:  # pragma: no cover - protocol
        ...

    async def insert_signals(
        self, signals: list[DistilledSignal],
    ) -> int:  # pragma: no cover - protocol
        ...


class NullSignalPersister:
    """`--dry-run` persister: records what would have been written
    but never touches a database. Lets the audit JSON still report
    "this run would have inserted N signals" without doing any I/O.
    """

    def __init__(self) -> None:
        self.would_have_inserted: list[DistilledSignal] = []

    async def existing_review_hashes_for_category(
        self, category: str,
    ) -> set[str]:
        return set()

    async def existing_signal_keys_for_category(
        self, category: str,
    ) -> set[tuple[str, str]]:
        return set()

    async def insert_signals(
        self, signals: list[DistilledSignal],
    ) -> int:
        self.would_have_inserted.extend(signals)
        return 0


class InMemorySignalPersister:
    """Test-only persister: keeps a list of inserted signals in
    memory + behaves correctly under dedup + resume."""

    def __init__(self) -> None:
        self.inserted: list[DistilledSignal] = []
        # Pre-population helpers for resume / dedup tests.
        self.preloaded_review_hashes: dict[str, set[str]] = {}
        self.preloaded_signal_keys: dict[str, set[tuple[str, str]]] = {}

    def preload_review_hashes(
        self, category: str, hashes: Iterable[str],
    ) -> None:
        self.preloaded_review_hashes.setdefault(category, set()).update(hashes)

    def preload_signal_keys(
        self, category: str, keys: Iterable[tuple[str, str]],
    ) -> None:
        self.preloaded_signal_keys.setdefault(category, set()).update(keys)

    async def existing_review_hashes_for_category(
        self, category: str,
    ) -> set[str]:
        existing = {
            s.source_review_hash for s in self.inserted
            if s.category == category
        }
        existing.update(self.preloaded_review_hashes.get(category, set()))
        return existing

    async def existing_signal_keys_for_category(
        self, category: str,
    ) -> set[tuple[str, str]]:
        existing = {
            (s.source_review_hash, s.signal_type) for s in self.inserted
            if s.category == category
        }
        existing.update(self.preloaded_signal_keys.get(category, set()))
        return existing

    async def insert_signals(
        self, signals: list[DistilledSignal],
    ) -> int:
        self.inserted.extend(signals)
        return len(signals)


# ---------------------------------------------------------------------------
# Pure ingestion loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryIngestPlan:
    category: str
    review_iter: Iterator[tuple[AmazonReviewRecord, str | None, str | None]]
    input_file: str | None
    distiller_config: DistillerConfig
    source_dataset: str = "amazon_reviews_2023"


async def ingest_category(
    plan: CategoryIngestPlan,
    persister: SignalPersister,
    *,
    dry_run: bool,
    resume: bool,
    limit: int | None = None,
    batch_size: int = 500,
) -> IngestionStats:
    """Run one category to completion (or `limit` rows).

    Returns the populated `IngestionStats` so the caller can write
    an audit JSON.
    """
    stats = IngestionStats(
        category=plan.category,
        input_file=plan.input_file,
        dry_run=dry_run,
    )

    existing_hashes = (
        await persister.existing_review_hashes_for_category(plan.category)
        if resume else set()
    )
    existing_keys = await persister.existing_signal_keys_for_category(
        plan.category,
    )

    pending: list[DistilledSignal] = []
    in_batch_keys: set[tuple[str, str]] = set()

    for record, title, brand in plan.review_iter:
        stats.rows_scanned += 1
        if limit is not None and stats.rows_scanned > limit:
            stats.rows_scanned -= 1  # didn't actually process this one
            break

        ok, reason = is_review_eligible(record, plan.distiller_config)
        if not ok:
            stats.record_rejection(record, reason or "unknown")
            continue

        # `existing_hashes` is empty unless resume=True. With resume
        # we skip the whole review (not just the signals) — saves
        # the distiller cost on a re-ingest.
        # We compute the hash *before* distillation by running the
        # distiller's own hash helper; since the distiller always
        # produces signals with the same hash for the same review,
        # we can peek at the first signal's hash. Use a cheap dummy
        # config so we get at least one signal in most cases — if
        # the distiller produces NO signals, we skip naturally.
        signals = distill_review_signals(
            record,
            config=plan.distiller_config,
            source_dataset=plan.source_dataset,
            product_title=title,
            brand=brand,
        )
        if not signals:
            # Review accepted by eligibility but no rule fired.
            # Count it as accepted-but-zero-signals so the audit can
            # explain why rows_accepted > 0 with no signals_generated.
            stats.rows_accepted += 1
            continue

        review_hash = signals[0].source_review_hash
        if resume and review_hash in existing_hashes:
            stats.rows_already_ingested += 1
            continue

        stats.record_accepted(signals)

        for sig in signals:
            key = (sig.source_review_hash, sig.signal_type)
            if key in existing_keys or key in in_batch_keys:
                stats.record_dup_skip()
                continue
            pending.append(sig)
            in_batch_keys.add(key)

        if len(pending) >= batch_size:
            n = await persister.insert_signals(pending)
            stats.record_inserted(n)
            pending = []

    if pending:
        n = await persister.insert_signals(pending)
        stats.record_inserted(n)

    stats.finished_at = time.time()
    return stats


__all__ = [
    "CategoryIngestPlan",
    "IngestionStats",
    "InMemorySignalPersister",
    "NullSignalPersister",
    "SignalPersister",
    "build_audit_payload",
    "ingest_category",
]
