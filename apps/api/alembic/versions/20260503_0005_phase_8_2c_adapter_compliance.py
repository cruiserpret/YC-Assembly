"""Phase 8.2C — adapter compliance gate table.

Revision ID: 0005_phase_8_2c
Revises: 0004_phase_8_2a
Create Date: 2026-05-03

Why:
  Phase 8.2C builds the safe ingestion framework — adapter base class,
  compliance gate, redaction pipeline, mocked adapter. Live ingestion
  is NOT activated. To make "you cannot run an adapter without explicit
  human approval" a structural rule (not a code convention), this
  migration adds `adapter_compliance_status`. Every adapter must register
  here; only `status='approved'` (with approver + approved_at populated)
  permits ingestion.

  The default for any newly-registered adapter is `status='draft'`. The
  Reddit Public API memo lives at status=draft after this phase ships —
  no live ingestion is authorized until a human flips it to 'approved'
  with explicit fields.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0005_phase_8_2c"
down_revision: str | None = "0004_phase_8_2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUS_VALUES = ("draft", "review", "approved", "suspended")


def upgrade() -> None:
    op.create_table(
        "adapter_compliance_status",
        sa.Column("adapter_name", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("memo_path", sa.Text(), nullable=False),
        sa.Column("approver", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"status IN {tuple(_STATUS_VALUES)!r}".replace("'", "'"),
            name="ck_adapter_compliance_status_value",
        ),
        # If status='approved', approver and approved_at MUST be populated.
        sa.CheckConstraint(
            "(status = 'approved' AND approver IS NOT NULL "
            "  AND approved_at IS NOT NULL) "
            "OR status <> 'approved'",
            name="ck_adapter_compliance_status_approval_fields",
        ),
    )


def downgrade() -> None:
    op.drop_table("adapter_compliance_status")
