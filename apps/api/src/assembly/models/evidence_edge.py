"""Phase 6.75 — typed relationships between evidence_items.

The graph is built once per simulation by `pipeline.evidence_graph.builder`.
Edges fall into three bases:

  - 'direct': deterministic edges derived from explicit data (e.g. two
    evidence rows have the same source_url → similar_to; competitor +
    pricing rows from the same brand → priced_against).
  - 'analogical': cross-market analogues; both endpoints are kind='analogical'.
  - 'inferred': LLM-derived support / contradiction / mapping edges. Per
    Correction 4, these are weak by themselves — they assist retrieval and
    ranking but cannot be the sole basis for a final-report claim.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk


# Closed enum mirrored in the migration.
EDGE_TYPES = (
    "supports",
    "contradicts",
    "similar_to",
    "causes_objection",
    "reduces_objection",
    "maps_to_segment",
    "maps_to_price_sensitivity",
    "maps_to_switching_trigger",
    "maps_to_trust_barrier",
    "maps_to_competitor",
    "maps_to_category_language",
    "maps_to_recommendation",
    "priced_against",
    "competes_with",
)

EDGE_BASIS = ("direct", "analogical", "inferred")


class EvidenceEdge(Base):
    """One typed edge between two evidence_items rows."""

    __tablename__ = "evidence_edges"
    __table_args__ = (
        UniqueConstraint(
            "simulation_id",
            "source_evidence_id",
            "target_evidence_id",
            "edge_type",
            name="uq_evidence_edges_unique_edge",
        ),
        CheckConstraint(
            "strength >= 0 AND strength <= 1",
            name="ck_evidence_edges_strength_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_evidence_edges_confidence_range",
        ),
        CheckConstraint(
            "source_evidence_id <> target_evidence_id",
            name="ck_evidence_edges_no_self_loop",
        ),
    )

    id: Mapped[UUIDPk]
    simulation_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_evidence_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_evidence_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edge_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    strength: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[CreatedAt]
