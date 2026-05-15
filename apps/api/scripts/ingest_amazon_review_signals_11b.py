"""Phase 11B — pilot-category Amazon Reviews ingestion CLI.

Reads ONE Amazon Reviews 2023 category file from the local on-disk
path (`ASSEMBLY_AMAZON_REVIEWS_DATA_DIR`), distills each accepted
review into Phase-11A signals, de-duplicates, and writes them into
the `amazon_review_signal` table.

This script is offline-only. It does NOT call any Amazon API, does
NOT scrape Amazon.com, and does NOT touch any production live flow.

USAGE

    # one-category dry-run (no DB writes)
    python -m scripts.ingest_amazon_review_signals_11b \
        --category Electronics --limit 1000 --dry-run

    # one-category commit
    python -m scripts.ingest_amazon_review_signals_11b \
        --category Electronics --commit

    # resume — skip review hashes already in the DB
    python -m scripts.ingest_amazon_review_signals_11b \
        --category Electronics --commit --resume

    # multiple categories in one invocation
    python -m scripts.ingest_amazon_review_signals_11b \
        --category Electronics All_Beauty Home_and_Kitchen --dry-run

PILOT CATEGORIES (operator-approved subset)

    Electronics
    All_Beauty
    Home_and_Kitchen
    Health_and_Household
    Sports_and_Outdoors

AUDIT OUTPUT

    apps/api/_audit/amazon_reviews_ingestion_11b_<category>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Iterator, Sequence

from assembly.sources.amazon_reviews_2023 import AmazonReviewRecord
from assembly.sources.amazon_reviews_provider import (
    AmazonReviewsProvider,
    AmazonReviewsProviderConfig,
    CategoryIngestPlan,
    DistillerConfig,
    IngestionStats,
    NullSignalPersister,
    SignalPersister,
    build_audit_payload,
    ingest_category,
)


PILOT_CATEGORIES: tuple[str, ...] = (
    "Electronics",
    "All_Beauty",
    "Home_and_Kitchen",
    "Health_and_Household",
    "Sports_and_Outdoors",
)


# ---------------------------------------------------------------------------
# Audit path helpers
# ---------------------------------------------------------------------------


def _audit_dir() -> Path:
    here = Path(__file__).resolve().parent.parent
    out = here / "_audit"
    out.mkdir(parents=True, exist_ok=True)
    return out


def audit_path_for(category: str) -> Path:
    safe = category.replace("/", "_").replace(" ", "_")
    return _audit_dir() / f"amazon_reviews_ingestion_11b_{safe}.json"


# ---------------------------------------------------------------------------
# Postgres persister (real DB writes)
# ---------------------------------------------------------------------------


class PostgresSignalPersister:
    """Real-DB persister.

    **Transaction semantics (batch-safe, not category-wide):**

      * Each call to :meth:`insert_signals` opens a fresh
        ``AsyncSession`` via ``async with``, runs one
        ``session.add_all`` + ``await session.commit()``, and closes
        the session. **That's one transaction per batch — not one per
        category.**
      * The ingestion loop in
        :func:`assembly.sources.amazon_reviews_provider.ingestion.ingest_category`
        flushes ``pending`` to the persister every ``batch_size``
        signals (default 500). So in a real run, every 500 signals
        get persisted-and-committed before the next 500 are
        distilled.
      * If the ingestion script crashes mid-category, every
        completed batch is already durably committed. Rerunning with
        ``--resume`` will skip review hashes already in the DB and
        pick up where the last batch left off.
      * Dedup is enforced at two layers: (a) the persister fetches
        ``existing_signal_keys_for_category`` once at start so any
        ``(source_review_hash, signal_type)`` already in the DB is
        skipped before insertion, and (b) the loop's
        ``in_batch_keys`` set prevents a single category run from
        re-emitting the same key twice. Reruns are therefore
        idempotent regardless of ``--resume``.

    Lazy-imports SQLAlchemy bits so a ``--dry-run`` invocation can
    run without a Postgres connection at all.
    """

    def __init__(self) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        from assembly.db import get_sessionmaker
        self._maker: async_sessionmaker[AsyncSession] = get_sessionmaker()

    async def existing_review_hashes_for_category(
        self, category: str,
    ) -> set[str]:
        from sqlalchemy import select
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        async with self._maker() as session:
            rows = await session.execute(
                select(AmazonReviewSignal.source_review_hash).where(
                    AmazonReviewSignal.category == category,
                ).distinct(),
            )
            return {r[0] for r in rows}

    async def existing_signal_keys_for_category(
        self, category: str,
    ) -> set[tuple[str, str]]:
        from sqlalchemy import select
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        async with self._maker() as session:
            rows = await session.execute(
                select(
                    AmazonReviewSignal.source_review_hash,
                    AmazonReviewSignal.signal_type,
                ).where(AmazonReviewSignal.category == category),
            )
            return {(h, t) for h, t in rows}

    async def insert_signals(
        self, signals: list,
    ) -> int:
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        if not signals:
            return 0
        async with self._maker() as session:
            session.add_all(
                [
                    AmazonReviewSignal(
                        source_dataset=s.source_dataset,
                        category=s.category,
                        product_title=s.product_title,
                        brand=s.brand,
                        asin=s.asin,
                        parent_asin=s.parent_asin,
                        rating=s.rating,
                        review_timestamp=s.review_timestamp,
                        verified_purchase=s.verified_purchase,
                        helpful_votes=s.helpful_votes,
                        sentiment_bucket=s.sentiment_bucket,
                        signal_type=s.signal_type,
                        theme=s.theme,
                        short_snippet=s.short_snippet,
                        competitor_mention=s.competitor_mention,
                        use_case=s.use_case,
                        source_review_hash=s.source_review_hash,
                    )
                    for s in signals
                ],
            )
            await session.commit()
        return len(signals)


# ---------------------------------------------------------------------------
# Plan builder — wraps the 11A provider's internal iterator
# ---------------------------------------------------------------------------


def build_plan_for_category(
    *,
    category: str,
    provider: AmazonReviewsProvider,
    distiller_config: DistillerConfig,
) -> CategoryIngestPlan:
    """Build a `CategoryIngestPlan` from the provider's public
    streaming iterator. We pull `(record, title, brand)` triples
    one at a time so RAM stays flat regardless of category size."""

    review_iter: Iterator[tuple[AmazonReviewRecord, str | None, str | None]]
    review_iter = provider.iter_category_reviews(
        category, require_enabled=True,
    )

    # Resolve the actual input file for the audit JSON. Cheap path
    # introspection; the provider already discovered this internally.
    input_file: str | None = None
    if provider.config.data_dir is not None:
        raw_dir = provider.config.data_dir / "raw"
        matches = sorted(raw_dir.glob(f"{category}*.jsonl*"))
        for m in matches:
            if "_meta" in m.name.lower():
                continue
            input_file = str(m)
            break

    return CategoryIngestPlan(
        category=category,
        review_iter=review_iter,
        input_file=input_file,
        distiller_config=distiller_config,
    )


# ---------------------------------------------------------------------------
# Main async run
# ---------------------------------------------------------------------------


async def _run(
    *,
    categories: Sequence[str],
    dry_run: bool,
    resume: bool,
    limit: int | None,
    data_dir: Path | None = None,
    persister: SignalPersister | None = None,
    audit_writer: "AuditWriter | None" = None,
    log: logging.Logger,
) -> list[IngestionStats]:
    """Run ingestion across `categories`. Returns the per-category
    stats list."""
    # Build provider config. We override the operator's
    # `ASSEMBLY_AMAZON_REVIEWS_ENABLED` flag to True for THIS script
    # only — Phase 11B is intentionally offline-only, so flipping
    # the flag in process memory here does not affect any live API
    # process. Production stays disabled.
    from assembly.config import get_settings
    settings = get_settings()
    resolved_dir = data_dir or (
        Path(settings.amazon_reviews_data_dir)
        if settings.amazon_reviews_data_dir else None
    )
    if resolved_dir is None:
        raise SystemExit(
            "ASSEMBLY_AMAZON_REVIEWS_DATA_DIR is unset and --data-dir "
            "was not passed. Cannot locate the dataset.",
        )
    provider_cfg = AmazonReviewsProviderConfig(
        enabled=True,
        data_dir=resolved_dir,
        categories=tuple(categories),
        max_items_per_run=limit if limit is not None
        else settings.amazon_reviews_max_items_per_run * 100,
        min_review_chars=settings.amazon_reviews_min_review_chars,
    )
    provider = AmazonReviewsProvider(provider_cfg)
    distiller_config = DistillerConfig(
        min_review_chars=settings.amazon_reviews_min_review_chars,
    )

    persister = persister or (
        NullSignalPersister() if dry_run else PostgresSignalPersister()
    )
    audit_writer = audit_writer or DiskAuditWriter()

    out: list[IngestionStats] = []
    for category in categories:
        log.info(
            "category=%s dry_run=%s resume=%s limit=%s",
            category, dry_run, resume, limit,
        )
        plan = build_plan_for_category(
            category=category,
            provider=provider,
            distiller_config=distiller_config,
        )
        if plan.input_file is None:
            log.warning(
                "category=%s no_review_file_found dir=%s",
                category, resolved_dir,
            )
        stats = await ingest_category(
            plan,
            persister,
            dry_run=dry_run,
            resume=resume,
            limit=limit,
        )
        out.append(stats)
        audit_writer.write(category, build_audit_payload(stats))
        log.info(
            "category=%s scanned=%d accepted=%d rejected=%d "
            "signals_generated=%d signals_inserted=%d "
            "dup_skipped=%d runtime=%.2fs",
            category,
            stats.rows_scanned,
            stats.rows_accepted,
            stats.rows_rejected,
            stats.signals_generated,
            stats.signals_inserted,
            stats.signals_skipped_duplicate,
            stats.runtime_seconds,
        )
    return out


# ---------------------------------------------------------------------------
# Audit writers (real vs in-memory test impl)
# ---------------------------------------------------------------------------


class AuditWriter:  # pragma: no cover - protocol shape
    def write(self, category: str, payload: dict[str, object]) -> None:
        raise NotImplementedError


class DiskAuditWriter(AuditWriter):
    def write(self, category: str, payload: dict[str, object]) -> None:
        path = audit_path_for(category)
        path.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8",
        )


class InMemoryAuditWriter(AuditWriter):
    """Test-only writer — keeps payloads in a dict so tests can
    assert against them without touching disk."""

    def __init__(self) -> None:
        self.payloads: dict[str, dict[str, object]] = {}

    def write(self, category: str, payload: dict[str, object]) -> None:
        self.payloads[category] = payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ingest_amazon_review_signals_11b",
        description=(
            "Phase 11B — distill Amazon Reviews 2023 into the "
            "amazon_review_signal table for a pilot category subset."
        ),
    )
    p.add_argument(
        "--category", "-c", nargs="+", required=True,
        help=(
            "One or more category names matching the on-disk "
            "filenames under <data-dir>/raw/. Pilot set: "
            + ", ".join(PILOT_CATEGORIES)
        ),
    )
    p.add_argument(
        "--limit", "-n", type=int, default=None,
        help="Max review rows scanned per category.",
    )
    p.add_argument(
        "--resume", action="store_true",
        help=(
            "Skip review hashes already present in the table for "
            "the same category. Safe to re-run."
        ),
    )
    p.add_argument(
        "--data-dir", type=Path, default=None,
        help=(
            "Override ASSEMBLY_AMAZON_REVIEWS_DATA_DIR for this "
            "invocation only."
        ),
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Default. Reads + distills + audits but never writes DB.",
    )
    mode.add_argument(
        "--commit", dest="dry_run", action="store_false",
        help="Persist distilled signals to amazon_review_signal.",
    )
    p.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress per-category info logs (errors still print).",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    log = logging.getLogger("phase_11b_ingest")
    t0 = time.time()
    asyncio.run(
        _run(
            categories=args.category,
            dry_run=args.dry_run,
            resume=args.resume,
            limit=args.limit,
            data_dir=args.data_dir,
            log=log,
        ),
    )
    log.info("phase_11b_total_runtime=%.2fs", time.time() - t0)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
