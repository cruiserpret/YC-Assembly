"""Phase 11B.6 — widen amazon_review_signal.review_timestamp to BIGINT.

Revision ID: 0013_phase_11_b_6
Revises: 0012_phase_11_a
Create Date: 2026-05-15

Why:
  Phase 11A declared `review_timestamp` as INTEGER (32-bit, max
  ~2.15B). The McAuley Lab Amazon Reviews 2023 dataset stores
  timestamps as milliseconds-since-epoch (13 digits, ~1.6 trillion),
  which overflows on insert.

  The Phase-11A synthetic test fixtures used 10-digit unix-seconds
  values (~1.7B) that fit INTEGER, so the bug never surfaced in
  the dry-run / unit-test layer. It only showed up the first time
  we tried to commit a real category run in Phase 11B.6.

  Fix: widen to BIGINT. ALTER COLUMN INTEGER -> BIGINT is a
  metadata-only change on PostgreSQL, safe against rows in flight.

  (A sibling Phase-11B.6 migration `0014_phase_11_b_6_product_title_text`
  widens `product_title` to TEXT for the same dataset-vs-fixture
  reason. Split into two revisions so a partially-migrated DB never
  ends up in a half-widened state.)
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013_phase_11_b_6"
down_revision: str | None = "0012_phase_11_a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "amazon_review_signal",
        "review_timestamp",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
        # USING clause not needed: INTEGER -> BIGINT is an implicit
        # widening cast in PostgreSQL.
    )


def downgrade() -> None:
    # Downgrade is lossy: any value > 2^31-1 will overflow back to
    # INTEGER and fail. We let PostgreSQL raise the error rather than
    # silently truncate.
    op.alter_column(
        "amazon_review_signal",
        "review_timestamp",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
