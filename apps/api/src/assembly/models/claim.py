"""Phase 6.75 — claims bound to evidence_items.

Every claim that will appear in the Phase 7 final report MUST live in this
table with a non-null `source_evidence_id`. The FK is `ON DELETE RESTRICT`
so a claim cannot be silently orphaned by deleting its source — it must be
explicitly relocated or removed.

Per Correction 4, LLM-inferred edges alone cannot back a claim: the
claim_validator (`pipeline.aggregation.claim_validator`) requires the
bound `source_evidence_id` to be a real evidence_items row, and the
`source_excerpt` must appear verbatim in that row's content.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk


CLAIM_TYPES = ("support", "contradiction", "observation", "objection")
CLAIM_BASIS = ("direct", "analogical")


class Claim(Base):
    """One claim bound to one evidence_items row."""

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_claims_confidence_range",
        ),
    )

    id: Mapped[UUIDPk]
    simulation_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_evidence_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    created_at: Mapped[CreatedAt]
