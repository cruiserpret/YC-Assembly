"""Phase 8.2C — `adapter_compliance_status` ORM model.

This is the structural compliance gate for every Population-Mode source
adapter. The default `status` for any registered adapter is `'draft'`;
flipping to `'approved'` requires both `approver` and `approved_at` to
be populated (enforced as a DB CHECK).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base


# Closed enum mirrored from the migration.
COMPLIANCE_STATUS_VALUES: tuple[str, ...] = (
    "draft", "review", "approved", "suspended",
)


class AdapterComplianceStatus(Base):
    """One row per source adapter. Adapters refuse to ingest unless
    `status='approved'` AND the approval fields are populated."""

    __tablename__ = "adapter_compliance_status"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','review','approved','suspended')",
            name="ck_adapter_compliance_status_value",
        ),
        CheckConstraint(
            "(status = 'approved' AND approver IS NOT NULL "
            "  AND approved_at IS NOT NULL) "
            "OR status <> 'approved'",
            name="ck_adapter_compliance_status_approval_fields",
        ),
    )

    adapter_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    memo_path: Mapped[str] = mapped_column(Text, nullable=False)
    approver: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
