"""Phase 11D.2 — PostgreSQL-backed `TechMarketSignalSource`.

Lives in a separate module so the in-process `retrieval` module stays
SQLAlchemy-free for cheap test imports. Production code path that
actually hits the DB pays for SQLAlchemy / asyncpg lazy-import only
when this class is instantiated.

Read-only. The retriever never writes to the table. The ingestion
CLI (Phase 11D.2) is the only writer, via
`TechMarketSignalPersister`.

Drift-tested invariants:

  * Read-only — only `select` statements.
  * No HTTP / scraping imports.
  * SQLAlchemy imports live inside method bodies (lazy).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from assembly.sources.tech_market_provider.retrieval import (
    TechMarketSignalSource,
    TechSignalRow,
)
from assembly.sources.tech_market_provider.signal_types import (
    MarketContext,
    SignalType,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


def _row_to_signal_row(row: object) -> TechSignalRow:
    """ORM row → narrow dataclass. Defensive about None for the
    metadata JSONB column (Postgres returns NULL as Python `None`)."""
    return TechSignalRow(
        source_provider=row.source_provider,  # type: ignore[attr-defined]
        source_category=row.source_category,  # type: ignore[attr-defined]
        product_category=row.product_category,  # type: ignore[attr-defined]
        company_or_product=row.company_or_product,  # type: ignore[attr-defined]
        competitor_name=row.competitor_name,  # type: ignore[attr-defined]
        signal_type=row.signal_type,  # type: ignore[attr-defined]
        sentiment_bucket=row.sentiment_bucket,  # type: ignore[attr-defined]
        buyer_type=row.buyer_type,  # type: ignore[attr-defined]
        market_context=row.market_context,  # type: ignore[attr-defined]
        theme=row.theme,  # type: ignore[attr-defined]
        short_snippet=row.short_snippet,  # type: ignore[attr-defined]
        evidence_url=row.evidence_url,  # type: ignore[attr-defined]
        source_timestamp=row.source_timestamp,  # type: ignore[attr-defined]
        relevance_score=row.relevance_score,  # type: ignore[attr-defined]
        metadata=(row.metadata_json or {}),  # type: ignore[attr-defined]
    )


def _base_order(stmt):  # type: ignore[no-untyped-def]
    """Shared deterministic ORDER BY for all read queries — higher
    relevance first, then most recent, then a stable tiebreak on
    short_snippet so test orderings are deterministic."""
    from assembly.models.tech_market_signal import TechMarketSignal
    return stmt.order_by(
        TechMarketSignal.relevance_score.desc().nulls_last(),
        TechMarketSignal.source_timestamp.desc().nulls_last(),
        TechMarketSignal.short_snippet,
    )


class PostgresTechMarketSignalSource:
    """Production `TechMarketSignalSource` — reads from the
    `tech_market_signal` table via SQLAlchemy.

    No writes ever happen here. The retriever path is strictly
    read-only.
    """

    def __init__(
        self, sessionmaker: "async_sessionmaker[AsyncSession]",
    ) -> None:
        self._sm = sessionmaker

    async def fetch_by_product_category(
        self,
        product_category: str,
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        from sqlalchemy import select
        from assembly.models.tech_market_signal import TechMarketSignal
        if not product_category or not product_category.strip():
            return []
        async with self._sm() as session:
            stmt = _base_order(
                select(TechMarketSignal).where(
                    TechMarketSignal.product_category == product_category,
                ),
            ).limit(limit)
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]

    async def fetch_by_market_context(
        self,
        market_context: MarketContext,
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        from sqlalchemy import select
        from assembly.models.tech_market_signal import TechMarketSignal
        async with self._sm() as session:
            stmt = _base_order(
                select(TechMarketSignal).where(
                    TechMarketSignal.market_context == market_context,
                ),
            ).limit(limit)
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]

    async def fetch_by_competitor(
        self,
        competitor: str,
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        from sqlalchemy import select, or_, func
        from assembly.models.tech_market_signal import TechMarketSignal
        needle = (competitor or "").strip().lower()
        if not needle:
            return []
        async with self._sm() as session:
            stmt = _base_order(
                select(TechMarketSignal).where(
                    or_(
                        func.lower(TechMarketSignal.competitor_name)
                        == needle,
                        func.lower(TechMarketSignal.company_or_product)
                        == needle,
                    ),
                ),
            ).limit(limit)
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]

    async def fetch_by_signal_types(
        self,
        signal_types: Sequence[SignalType],
        *,
        limit: int,
    ) -> list[TechSignalRow]:
        from sqlalchemy import select
        from assembly.models.tech_market_signal import TechMarketSignal
        if not signal_types:
            return []
        async with self._sm() as session:
            stmt = _base_order(
                select(TechMarketSignal).where(
                    TechMarketSignal.signal_type.in_(signal_types),
                ),
            ).limit(limit)
            res = await session.execute(stmt)
            return [_row_to_signal_row(r) for r in res.scalars().all()]


__all__ = ["PostgresTechMarketSignalSource"]


# Static type check: confirms the class satisfies the Protocol.
_check: TechMarketSignalSource = (
    PostgresTechMarketSignalSource.__new__(  # type: ignore[abstract]
        PostgresTechMarketSignalSource,
    )
)
del _check
