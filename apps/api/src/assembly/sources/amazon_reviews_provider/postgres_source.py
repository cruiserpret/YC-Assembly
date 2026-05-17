"""Phase 11C.1 — PostgreSQL-backed `SignalSource` implementation.

Lives in a separate module so the in-process `retrieval` module can
import the protocol + dataclasses cheaply (no SQLAlchemy import),
while the production code path that actually hits the DB pays for
SQLAlchemy / asyncpg lazy-import only when it's instantiated.

Read-only. The retriever never writes to the table. The ingestion
script (Phase 11B) is the only writer.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from assembly.sources.amazon_reviews_provider.retrieval import (
    SignalRow,
    SignalSource,
)
from assembly.sources.amazon_reviews_provider.signal_types import SignalType

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


def _row_to_signal_row(row: object) -> SignalRow:
    return SignalRow(
        signal_type=row.signal_type,  # type: ignore[attr-defined]
        sentiment_bucket=row.sentiment_bucket,  # type: ignore[attr-defined]
        theme=row.theme,  # type: ignore[attr-defined]
        category=row.category,  # type: ignore[attr-defined]
        brand=row.brand,  # type: ignore[attr-defined]
        product_title=row.product_title,  # type: ignore[attr-defined]
        asin=row.asin,  # type: ignore[attr-defined]
        parent_asin=row.parent_asin,  # type: ignore[attr-defined]
        rating=row.rating,  # type: ignore[attr-defined]
        verified_purchase=row.verified_purchase,  # type: ignore[attr-defined]
        helpful_votes=row.helpful_votes,  # type: ignore[attr-defined]
        short_snippet=row.short_snippet,  # type: ignore[attr-defined]
        competitor_mention=row.competitor_mention,  # type: ignore[attr-defined]
        use_case=row.use_case,  # type: ignore[attr-defined]
        source_review_hash=row.source_review_hash,  # type: ignore[attr-defined]
    )


class PostgresSignalSource:
    """Production `SignalSource` — reads from the `amazon_review_signal`
    table via SQLAlchemy.

    No writes ever happen here. The retriever path is strictly
    read-only.
    """

    def __init__(self, sessionmaker: "async_sessionmaker[AsyncSession]") -> None:
        self._sm = sessionmaker

    async def fetch_by_category(
        self, category: str, *, limit: int,
    ) -> list[SignalRow]:
        from sqlalchemy import select
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        async with self._sm() as session:
            stmt = (
                select(AmazonReviewSignal)
                .where(AmazonReviewSignal.category == category)
                # verified purchase first, then helpful_votes desc.
                .order_by(
                    AmazonReviewSignal.verified_purchase.desc().nulls_last(),
                    AmazonReviewSignal.helpful_votes.desc().nulls_last(),
                    AmazonReviewSignal.source_review_hash,
                )
                .limit(limit)
            )
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]

    async def fetch_by_competitor(
        self, competitor: str, *, limit: int,
    ) -> list[SignalRow]:
        from sqlalchemy import select, or_, func
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        needle = competitor.strip().lower()
        if not needle:
            return []
        async with self._sm() as session:
            stmt = (
                select(AmazonReviewSignal)
                .where(
                    or_(
                        func.lower(AmazonReviewSignal.competitor_mention)
                        == needle,
                        func.lower(AmazonReviewSignal.brand) == needle,
                    ),
                )
                .order_by(
                    AmazonReviewSignal.verified_purchase.desc().nulls_last(),
                    AmazonReviewSignal.helpful_votes.desc().nulls_last(),
                    AmazonReviewSignal.source_review_hash,
                )
                .limit(limit)
            )
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]

    async def fetch_by_theme(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int,
    ) -> list[SignalRow]:
        from sqlalchemy import select
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        if not signal_types:
            return []
        async with self._sm() as session:
            stmt = (
                select(AmazonReviewSignal)
                .where(AmazonReviewSignal.signal_type.in_(signal_types))
                .order_by(
                    AmazonReviewSignal.verified_purchase.desc().nulls_last(),
                    AmazonReviewSignal.helpful_votes.desc().nulls_last(),
                    AmazonReviewSignal.source_review_hash,
                )
                .limit(limit)
            )
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]

    async def fetch_by_title_keyword(
        self,
        keyword: str,
        *,
        category: str | None = None,
        limit: int,
    ) -> list[SignalRow]:
        """Phase 11C.7 — case-insensitive substring match on
        `product_title`. Backed by `LOWER(product_title) LIKE
        '%keyword%'`. Postgres falls back to a sequential scan; the
        per-keyword caller fan-out keeps total work bounded by
        `title_keyword_pool_limit` across all keywords.
        """
        from sqlalchemy import select, func
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        needle = (keyword or "").strip().lower()
        if not needle:
            return []
        pattern = f"%{needle}%"
        async with self._sm() as session:
            stmt = select(AmazonReviewSignal).where(
                func.lower(AmazonReviewSignal.product_title).like(pattern),
            )
            if category:
                stmt = stmt.where(
                    AmazonReviewSignal.category == category,
                )
            stmt = stmt.order_by(
                AmazonReviewSignal.verified_purchase.desc().nulls_last(),
                AmazonReviewSignal.helpful_votes.desc().nulls_last(),
                AmazonReviewSignal.source_review_hash,
            ).limit(limit)
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]

    async def fetch_by_brand_substring(
        self,
        brand: str,
        *,
        category: str | None = None,
        limit: int,
    ) -> list[SignalRow]:
        """Phase 11C.7 — case-insensitive substring match against
        `brand`, `competitor_mention`, OR `product_title`. Catches
        cases where a competitor name appears in the title even if
        the brand column is unset (common in McAuley data).
        """
        from sqlalchemy import select, func, or_
        from assembly.models.amazon_review_signal import AmazonReviewSignal
        needle = (brand or "").strip().lower()
        if not needle:
            return []
        pattern = f"%{needle}%"
        async with self._sm() as session:
            stmt = select(AmazonReviewSignal).where(
                or_(
                    func.lower(AmazonReviewSignal.brand).like(pattern),
                    func.lower(
                        AmazonReviewSignal.competitor_mention,
                    ).like(pattern),
                    func.lower(
                        AmazonReviewSignal.product_title,
                    ).like(pattern),
                ),
            )
            if category:
                stmt = stmt.where(
                    AmazonReviewSignal.category == category,
                )
            stmt = stmt.order_by(
                AmazonReviewSignal.verified_purchase.desc().nulls_last(),
                AmazonReviewSignal.helpful_votes.desc().nulls_last(),
                AmazonReviewSignal.source_review_hash,
            ).limit(limit)
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]


__all__ = ["PostgresSignalSource"]


# Static type check: confirms PostgresSignalSource satisfies the
# SignalSource Protocol.
_check: SignalSource = PostgresSignalSource.__new__(PostgresSignalSource)  # type: ignore[abstract]
del _check
