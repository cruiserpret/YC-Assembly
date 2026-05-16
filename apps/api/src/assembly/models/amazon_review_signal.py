"""Phase 11A — distilled Amazon-review buyer-language signal table.

This table never stores full raw review bodies. The columns are
deliberately narrow: one row = one distilled signal extracted from
one source review. The raw review row stays in its on-disk dataset
file; the provider hashes the source review's identity into
`source_review_hash` so we can de-dup across ingestion runs without
storing the original `user_id`.

`sentiment_bucket` and `signal_type` are validated by CHECK
constraints so bad ingestion code can't sneak unknown values past
the database.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk


SENTIMENT_BUCKETS: tuple[str, ...] = ("positive", "negative", "mixed")

SIGNAL_TYPES: tuple[str, ...] = (
    "objection",
    "praise",
    "proof_need",
    "switch_reason",
    "return_reason",
    "durability",
    "price",
    "trust",
    "safety",
    "setup",
    "support",
    "use_case",
)


class AmazonReviewSignal(Base):
    __tablename__ = "amazon_review_signal"
    __table_args__ = (
        CheckConstraint(
            "sentiment_bucket IN ('positive','negative','mixed')",
            name="ck_amazon_review_signal_sentiment_bucket",
        ),
        CheckConstraint(
            "signal_type IN ('objection','praise','proof_need',"
            "'switch_reason','return_reason','durability','price',"
            "'trust','safety','setup','support','use_case')",
            name="ck_amazon_review_signal_signal_type",
        ),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_amazon_review_signal_rating_range",
        ),
        Index("ix_amazon_review_signal_category", "category"),
        Index("ix_amazon_review_signal_signal_type", "signal_type"),
        Index(
            "ix_amazon_review_signal_source_review_hash",
            "source_review_hash",
        ),
    )

    id: Mapped[UUIDPk]
    source_dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(96), nullable=False)
    # Amazon listing titles can exceed 1,900 characters in some
    # categories (Health_and_Personal_Care + All_Beauty observed in
    # real McAuley 2023 data). Use unbounded Text so we never have to
    # truncate persona-grade product attribution. Phase 11B.6 fix.
    product_title: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_asin: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # McAuley Amazon Reviews 2023 timestamps are milliseconds-since-epoch
    # (~13 digits, e.g. 1602133857705) which overflow a 32-bit Integer.
    # BigInteger handles both ms (2023 dataset) and seconds (2018 / older
    # snapshots) without precision loss. Phase 11B.6 fix.
    review_timestamp: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True,
    )
    verified_purchase: Mapped[bool | None] = mapped_column(nullable=True)
    helpful_votes: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    sentiment_bucket: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    theme: Mapped[str | None] = mapped_column(String(96), nullable=True)
    short_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    competitor_mention: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    use_case: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    # SHA-256 first-16-hex of the source review's (category|asin|user_id_hash|
    # timestamp|first-128-text-chars) tuple — lets ingestion de-dup
    # signals across re-runs without storing the original review.
    source_review_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    created_at: Mapped[CreatedAt]


__all__ = [
    "AmazonReviewSignal",
    "SENTIMENT_BUCKETS",
    "SIGNAL_TYPES",
]
