"""Phase 6.5 — widen simulations.status, add progress/parsed_pio/failed_stage.

Revision ID: 0002_phase_6_5
Revises: 0001_initial
Create Date: 2026-05-01

Why:
  Phase 6.5 introduces granular status names ("simulation_completed",
  "evidence_building", "society_building") that exceed the original
  VARCHAR(16). Plus three new columns:
    - progress JSONB         — operational progress object for status polls
    - parsed_pio JSONB       — cached intake parser output (idempotent retry)
    - failed_stage VARCHAR   — which orchestration stage failed
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase_6_5"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Widen status from VARCHAR(16) to VARCHAR(32).
    op.alter_column(
        "simulations",
        "status",
        type_=sa.String(32),
        existing_type=sa.String(16),
        existing_nullable=False,
        existing_server_default="pending",
    )

    # 2) Add progress JSONB.
    op.add_column(
        "simulations",
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # 3) Add parsed_pio JSONB.
    op.add_column(
        "simulations",
        sa.Column(
            "parsed_pio",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # 4) Add failed_stage VARCHAR(32).
    op.add_column(
        "simulations",
        sa.Column("failed_stage", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("simulations", "failed_stage")
    op.drop_column("simulations", "parsed_pio")
    op.drop_column("simulations", "progress")
    op.alter_column(
        "simulations",
        "status",
        type_=sa.String(16),
        existing_type=sa.String(32),
        existing_nullable=False,
        existing_server_default="pending",
    )
