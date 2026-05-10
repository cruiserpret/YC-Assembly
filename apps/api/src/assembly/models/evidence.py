from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDFk, UUIDPk

if TYPE_CHECKING:
    from assembly.models.simulation import Simulation


# kind: real-vs-simulated separation
EVIDENCE_KINDS = ("direct", "analogical", "missing")

# source_type: where the evidence came from
EVIDENCE_SOURCE_TYPES = (
    "user_input",
    "competitor_page",
    "public_review",
    "category_language",
    "pricing_page",
    "analogical_market",
)

# Phase 6.75: closed enum mirrored in alembic 0003 migration.
NODE_CLASSES = (
    "competitor",
    "pricing",
    "review",
    "buyer_pain",
    "objection",
    "claim",
    "claim_risk",
    "claim_support",
    "category_language",
    "current_alternative",
    "switching_trigger",
    "trust_barrier",
    "analogical_market",
    "segment_behavior",
    "unknown",
)


class EvidenceItem(Base):
    """Single evidence atom. `kind=direct` and `kind=analogical` MUST trace to a real
    source (user input or fetched URL). `kind=missing` records the gap explicitly so
    the evidence ledger can surface what we don't have.

    Phase 6.75 columns:
      - node_class / node_class_confidence — graph classification
      - content_hash — sha-equivalent hash of normalized content; powers dedup
      - dedup_group_id — populated by the dedup pass; collapse-on-read in retriever
      - embedding / embedding_model / embedded_at — pgvector + provenance
    """

    __tablename__ = "evidence_items"

    id: Mapped[UUIDPk]
    simulation_id: Mapped[UUIDFk] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    # Phase 6.75 — graph classification
    node_class: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", index=True
    )
    node_class_confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("0.0")
    )

    # Phase 6.75 — content hashing + dedup. content_hash is non-null at the
    # DB level; we leave the column type Optional in Python so SQLAlchemy
    # doesn't require it pre-flush — the migration backfill + DB default
    # ensure it's always populated for inserted rows.
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=False, default=""
    )
    dedup_group_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Phase 6.75 — embedding (optional). The pgvector column is added at the
    # migration level; we keep the Python attribute as a list[float] for
    # ergonomic access via SQLAlchemy without importing pgvector here.
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[CreatedAt]

    simulation: Mapped[Simulation] = relationship(back_populates="evidence")
