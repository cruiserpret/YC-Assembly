"""Phase 9D — ORM models for the cohort/cluster architecture.

Mirrors the schema introduced by alembic revision 0009_phase_9_d.
Three additive tables — no mutation of any 9A/9B row.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk


CLUSTERING_METHODS: tuple[str, ...] = (
    "deterministic_agglomerative_v1", "external_seed", "manual_audit",
)
EVIDENCE_ROLES: tuple[str, ...] = (
    "objection", "proof_need", "stance_anchor", "psychology_anchor",
    "discussion_anchor", "evidence_anchor",
)


class SocietyCohort(Base):
    __tablename__ = "society_cohorts"
    __table_args__ = (
        CheckConstraint(
            "cohort_size >= 1",
            name="ck_society_cohorts_size_positive",
        ),
        CheckConstraint(
            "cohort_weight >= 0 AND cohort_weight <= 1",
            name="ck_society_cohorts_weight_range",
        ),
        CheckConstraint(
            "clustering_method IN ('deterministic_agglomerative_v1',"
            "'external_seed','manual_audit')",
            name="ck_society_cohorts_clustering_method",
        ),
        Index(
            "ix_society_cohorts_run_phase",
            "run_scope_id", "phase",
        ),
    )

    id: Mapped[UUIDPk]
    run_scope_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    cohort_label: Mapped[str] = mapped_column(String(128), nullable=False)
    cohort_size: Mapped[int] = mapped_column(Integer, nullable=False)
    cohort_weight: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False,
    )
    representative_persona_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    member_persona_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False,
    )
    clustering_method: Mapped[str] = mapped_column(
        String(64), nullable=False,
    )
    role_distribution: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    stance_distribution: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    psychology_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    objection_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    proof_need_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    discussion_behavior_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    caveats: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    created_at: Mapped[CreatedAt]


class SocietyCohortEvidenceLink(Base):
    """Traceability row binding a cohort claim to a real DB origin.

    DB CHECK enforces at least one of (source_record_id,
    discussion_turn_id, memory_atom_id, persona_id) is non-null. Excerpt
    must be non-empty. Closed-set `evidence_role`.
    """

    __tablename__ = "society_cohort_evidence_links"
    __table_args__ = (
        CheckConstraint(
            "evidence_role IN ('objection','proof_need','stance_anchor',"
            "'psychology_anchor','discussion_anchor','evidence_anchor')",
            name="ck_society_cohort_evidence_links_role",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_society_cohort_evidence_links_confidence",
        ),
        CheckConstraint(
            "(source_record_id IS NOT NULL) "
            "OR (discussion_turn_id IS NOT NULL) "
            "OR (memory_atom_id IS NOT NULL) "
            "OR (persona_id IS NOT NULL)",
            name="ck_society_cohort_evidence_links_at_least_one_origin",
        ),
        CheckConstraint(
            "char_length(excerpt) >= 1",
            name="ck_society_cohort_evidence_links_excerpt_nonempty",
        ),
    )

    id: Mapped[UUIDPk]
    cohort_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("society_cohorts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_record_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("source_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    discussion_turn_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("discussion_turns.id", ondelete="SET NULL"),
        nullable=True,
    )
    memory_atom_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_memory_atoms.id", ondelete="SET NULL"),
        nullable=True,
    )
    persona_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    evidence_role: Mapped[str] = mapped_column(String(32), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("0.5"),
    )
    created_at: Mapped[CreatedAt]


class SocietyCohortRollup(Base):
    __tablename__ = "society_cohort_rollups"
    __table_args__ = (
        UniqueConstraint(
            "run_scope_id", "phase",
            name="uq_society_cohort_rollups_run_phase",
        ),
        CheckConstraint(
            "cohort_count >= 1",
            name="ck_society_cohort_rollups_cohort_count",
        ),
        CheckConstraint(
            "persona_count >= 1",
            name="ck_society_cohort_rollups_persona_count",
        ),
    )

    id: Mapped[UUIDPk]
    run_scope_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    cohort_count: Mapped[int] = mapped_column(Integer, nullable=False)
    persona_count: Mapped[int] = mapped_column(Integer, nullable=False)
    weighted_stance_distribution: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    weighted_objection_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    weighted_proof_need_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    social_influence_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    resistance_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    caveats: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    quality_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    created_at: Mapped[CreatedAt]
