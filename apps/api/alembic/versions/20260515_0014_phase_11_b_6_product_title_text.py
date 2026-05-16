"""Phase 11B.6 — widen amazon_review_signal.product_title to TEXT.

Revision ID: 0014_phase_11_b_6
Revises: 0013_phase_11_b_6
Create Date: 2026-05-15

Why:
  Phase 11A declared `product_title` as VARCHAR(512). Real Amazon
  listings can have titles well above that bound — observed maxima
  in the McAuley Lab Amazon Reviews 2023 metadata files:

    Category                       max title chars
    -----------------------------  ---------------
    Health_and_Personal_Care                1,901
    All_Beauty                              1,347
    Industrial_and_Scientific                 504+
    Software                                  449
    Subscription_Boxes (no meta)              n/a

  VARCHAR(512) truncated several Industrial_and_Scientific titles
  and aborted the commit with `StringDataRightTruncationError`.
  TEXT is unbounded in PostgreSQL.

  Split from the sister timestamp-widening migration
  (0013_phase_11_b_6) so a partially-migrated DB never ends up
  half-widened. Both must apply for Phase 11B.6 commit ingestion
  to succeed.

  ALTER COLUMN VARCHAR(n) -> TEXT is a metadata-only change in
  PostgreSQL; no row rewrite needed. Safe against tables that
  already contain rows.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0014_phase_11_b_6"
down_revision: str | None = "0013_phase_11_b_6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "amazon_review_signal",
        "product_title",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Downgrade is lossy: any title > 512 chars will raise on insert
    # after the column is narrowed. We don't truncate silently.
    op.alter_column(
        "amazon_review_signal",
        "product_title",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
