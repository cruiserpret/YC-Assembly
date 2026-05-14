"""Phase 11A — distilled amazon_review_signal table.

Revision ID: 0012_phase_11_a
Revises: 0011_phase_10_a
Create Date: 2026-05-14

Why:
  Phase 11A adds an Amazon Reviews ingestion provider that distills
  raw reviews into structured buyer-language signals (objections,
  praise, switch reasons, etc.). The full Phase 8.5 raw-review reader
  stays local-only and never persists; only the distilled signals
  produced by this Phase-11A provider land in Postgres.

  The table is additive and ships empty. The provider remains gated
  off by default (`ASSEMBLY_AMAZON_REVIEWS_ENABLED=false`), so the
  table simply sits unused until Phase 11B kicks off real ingestion.

Tables (additive only):
  amazon_review_signal — one row per distilled buyer-language signal
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0012_phase_11_a"
down_revision: str | None = "0011_phase_10_a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SENTIMENT_BUCKETS = ("positive", "negative", "mixed")
_SIGNAL_TYPES = (
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


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "amazon_review_signal",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_dataset", sa.String(64), nullable=False),
        sa.Column("category", sa.String(96), nullable=False),
        sa.Column("product_title", sa.String(512), nullable=True),
        sa.Column("brand", sa.String(128), nullable=True),
        sa.Column("asin", sa.String(32), nullable=True),
        sa.Column("parent_asin", sa.String(32), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("review_timestamp", sa.Integer(), nullable=True),
        sa.Column("verified_purchase", sa.Boolean(), nullable=True),
        sa.Column("helpful_votes", sa.Integer(), nullable=True),
        sa.Column("sentiment_bucket", sa.String(16), nullable=False),
        sa.Column("signal_type", sa.String(32), nullable=False),
        sa.Column("theme", sa.String(96), nullable=True),
        sa.Column("short_snippet", sa.Text(), nullable=False),
        sa.Column("competitor_mention", sa.String(128), nullable=True),
        sa.Column("use_case", sa.String(128), nullable=True),
        sa.Column("source_review_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"sentiment_bucket IN {_in_clause(_SENTIMENT_BUCKETS)}",
            name="ck_amazon_review_signal_sentiment_bucket",
        ),
        sa.CheckConstraint(
            f"signal_type IN {_in_clause(_SIGNAL_TYPES)}",
            name="ck_amazon_review_signal_signal_type",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_amazon_review_signal_rating_range",
        ),
    )
    op.create_index(
        "ix_amazon_review_signal_category",
        "amazon_review_signal",
        ["category"],
    )
    op.create_index(
        "ix_amazon_review_signal_signal_type",
        "amazon_review_signal",
        ["signal_type"],
    )
    op.create_index(
        "ix_amazon_review_signal_source_review_hash",
        "amazon_review_signal",
        ["source_review_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_amazon_review_signal_source_review_hash",
        table_name="amazon_review_signal",
    )
    op.drop_index(
        "ix_amazon_review_signal_signal_type",
        table_name="amazon_review_signal",
    )
    op.drop_index(
        "ix_amazon_review_signal_category",
        table_name="amazon_review_signal",
    )
    op.drop_table("amazon_review_signal")
