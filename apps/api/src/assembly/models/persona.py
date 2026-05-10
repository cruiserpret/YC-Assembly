"""Phase 8.2A — ORM models for the Population Mode foundation.

Schema-only contract:
  - Anonymous personas (random display_name; NO real-identity columns).
  - Per-field values with support_level enforcement.
  - Sources are immutable + compliance-tagged.
  - Required PopulationConstructionAudit row per Population-Mode run.

NO ingestion code, NO simulation code, NO API surface here. This module is
the storage contract that future ingestion / retrieval / simulation phases
build on top of.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk

if TYPE_CHECKING:
    pass


# Closed enums — keep in sync with the alembic migration AND with
# `pipeline/persona/constants.py`. The migration enforces these as DB
# CHECK constraints; constants.py is the Python source of truth.


class SourceRecord(Base):
    """Immutable snapshot from a public source. Compliance-tagged.

    Phase 8.2A creates the schema; no adapters write to this table yet.
    Future ingestion code MUST set `compliance_tag` from the closed set
    defined in `pipeline/persona/constants.py::COMPLIANCE_TAGS`.

    No raw handle column. No raw name column. No photo column. No email
    column. The `user_handle_hash` column stores a salted hash for source
    de-duplication; the raw handle is never stored.
    """

    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint(
            "source_kind", "content_hash",
            name="uq_source_records_kind_hash",
        ),
        CheckConstraint(
            "compliance_tag IN ('public_api','public_html','open_dataset',"
            "'open_aggregate','manual_seed')",
            name="ck_source_records_compliance_tag",
        ),
    )

    id: Mapped[UUIDPk]
    source_kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict,
    )
    ingested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    compliance_tag: Mapped[str] = mapped_column(String(48), nullable=False)
    user_handle_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pii_redaction_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_run",
    )
    sensitive_scan_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_run",
    )
    created_at: Mapped[CreatedAt]


class PersonaRecord(Base):
    """Anonymous synthetic society node.

    `display_name` is random/fictional and MUST NOT be sourced from any
    real-world identity. `pipeline/persona/anonymization.generate_display_name`
    is the only blessed origin. No real-identity columns exist here:
    no email, phone, address, photo, or profile URL.
    """

    __tablename__ = "persona_records"
    __table_args__ = (
        CheckConstraint(
            "influence_score IS NULL "
            "OR (influence_score >= 0 AND influence_score <= 1)",
            name="ck_persona_records_influence_score_range",
        ),
        CheckConstraint(
            "susceptibility IS NULL "
            "OR (susceptibility >= 0 AND susceptibility <= 1)",
            name="ck_persona_records_susceptibility_range",
        ),
    )

    id: Mapped[UUIDPk]
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_label: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
    )
    origin_market_broad: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True,
    )
    product_relevance_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    influence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), nullable=True,
    )
    susceptibility: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), nullable=True,
    )
    population_weight: Mapped[Decimal] = mapped_column(
        Numeric(8, 3), nullable=False, default=Decimal("1.0"),
    )
    source_strength_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), nullable=True,
    )
    created_at: Mapped[CreatedAt]
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


class PersonaTrait(Base):
    """Per-(persona, field_name) value with support-level enforcement.

    The DB CHECK constraint guarantees:
      - direct/inferred ⇒ source_ids non-empty AND value not null AND confidence > 0
      - unknown ⇒ value null AND source_ids empty
      - missing ⇒ value null
    Python validators in `pipeline/persona/validator.py` add the stricter
    inferred-confidence ≥ 0.5 rule and the field-name allow-list check
    (also enforced as a DB CHECK).
    """

    __tablename__ = "persona_traits"
    __table_args__ = (
        UniqueConstraint(
            "persona_id", "field_name",
            name="uq_persona_traits_persona_field",
        ),
        CheckConstraint(
            "support_level IN ('direct','inferred','unknown','missing')",
            name="ck_persona_traits_support_level",
        ),
        CheckConstraint(
            "field_name IN ('interests','role_or_context','buying_constraints',"
            "'trust_triggers','current_alternatives','communication_style',"
            "'influence_signals','price_sensitivity','objection_patterns',"
            "'geography_broad')",
            name="ck_persona_traits_field_name",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_persona_traits_confidence_range",
        ),
        CheckConstraint(
            "(support_level IN ('direct','inferred') "
            "  AND cardinality(source_ids) >= 1 "
            "  AND value IS NOT NULL "
            "  AND confidence > 0) "
            "OR (support_level = 'unknown' "
            "    AND value IS NULL "
            "    AND cardinality(source_ids) = 0) "
            "OR (support_level = 'missing' "
            "    AND value IS NULL)",
            name="ck_persona_traits_support_consistency",
        ),
    )

    id: Mapped[UUIDPk]
    persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    support_level: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("0.0"),
    )
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


class PersonaEvidenceLink(Base):
    """Bind every supported persona trait to the source row that supports it."""

    __tablename__ = "persona_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "persona_id", "source_record_id", "contribution_field",
            name="uq_persona_evidence_links_unique_contribution",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_persona_evidence_links_confidence_range",
        ),
        CheckConstraint(
            "contribution_field IN ('interests','role_or_context',"
            "'buying_constraints','trust_triggers','current_alternatives',"
            "'communication_style','influence_signals','price_sensitivity',"
            "'objection_patterns','geography_broad')",
            name="ck_persona_evidence_links_field",
        ),
    )

    id: Mapped[UUIDPk]
    persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_record_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("source_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    contribution_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    contribution_field: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    created_at: Mapped[CreatedAt]


class PersonaOpinion(Base):
    """Future per-simulation persona stance row. Schema-only in 8.2A."""

    __tablename__ = "persona_opinions"
    __table_args__ = (
        UniqueConstraint(
            "persona_id", "simulation_id",
            name="uq_persona_opinions_persona_simulation",
        ),
    )

    id: Mapped[UUIDPk]
    persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    simulation_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    stance: Mapped[str] = mapped_column(String(32), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    cluster_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_representative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    propagation_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    influenced_by_persona_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[CreatedAt]


class PersonaGraphEdge(Base):
    """Future similarity / influence edge between two personas."""

    __tablename__ = "persona_graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_persona_id", "target_persona_id", "edge_type",
            name="uq_persona_graph_edges_unique_edge",
        ),
        CheckConstraint(
            "source_persona_id <> target_persona_id",
            name="ck_persona_graph_edges_no_self_loop",
        ),
        CheckConstraint(
            "strength >= 0 AND strength <= 1",
            name="ck_persona_graph_edges_strength_range",
        ),
        CheckConstraint(
            "edge_type IN ('similar_to','influences','shares_segment',"
            "'shared_source','bridge_to')",
            name="ck_persona_graph_edges_edge_type",
        ),
        CheckConstraint(
            "basis IN ('embedding_cosine','shared_source','deterministic','inferred')",
            name="ck_persona_graph_edges_basis",
        ),
    )

    id: Mapped[UUIDPk]
    source_persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    target_persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    strength: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[CreatedAt]


class PersonaCluster(Base):
    """Future community-detection output."""

    __tablename__ = "persona_clusters"

    id: Mapped[UUIDPk]
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dominant_objection_pattern: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    dominant_persuasion_driver: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    created_at: Mapped[CreatedAt]


class PersonaClusterMembership(Base):
    """Many-to-many: persona ↔ cluster."""

    __tablename__ = "persona_cluster_membership"
    __table_args__ = (
        PrimaryKeyConstraint(
            "persona_id", "cluster_id",
            name="pk_persona_cluster_membership",
        ),
    )

    persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    cluster_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_clusters.id", ondelete="CASCADE"),
        nullable=False,
    )


class AudienceRetrievalRun(Base):
    """Future audit log per Population-Mode audience retrieval."""

    __tablename__ = "audience_retrieval_runs"
    __table_args__ = (
        CheckConstraint(
            "geography_coverage_label IS NULL "
            "OR geography_coverage_label IN ('thin','moderate','strong')",
            name="ck_audience_retrieval_runs_geography_coverage_label",
        ),
    )

    id: Mapped[UUIDPk]
    simulation_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retrieved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    filtered_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ranking_signals: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    geography_coverage_label: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
    )
    created_at: Mapped[CreatedAt]


class PopulationConstructionAudit(Base):
    """REQUIRED artifact per Population-Mode simulation. The audit panel in
    the Phase 8.2 UI surfaces this row at the top of every Population
    report — society size, source-kind counts, trait-support breakdown,
    geography coverage label, missing-evidence warnings, society strength.

    Phase 8.2A creates the schema only; future Population-Mode runs will
    populate it. The Pydantic builder + validator live in
    `pipeline/persona/audit.py`.
    """

    __tablename__ = "population_construction_audit"
    __table_args__ = (
        CheckConstraint(
            "geography_coverage_label IN ('thin','moderate','strong')",
            name="ck_population_audit_geography_coverage_label",
        ),
        CheckConstraint(
            "source_freshness_label IS NULL "
            "OR source_freshness_label IN ('thin','moderate','strong')",
            name="ck_population_audit_source_freshness_label",
        ),
        CheckConstraint(
            "society_strength_label IN ('thin','moderate','strong')",
            name="ck_population_audit_society_strength_label",
        ),
        CheckConstraint(
            "retrieved_persona_count >= 0 "
            "AND final_persona_count >= 0 "
            "AND cluster_count >= 0",
            name="ck_population_audit_counts_nonneg",
        ),
    )

    id: Mapped[UUIDPk]
    simulation_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    requested_society: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retrieved_persona_count: Mapped[int] = mapped_column(Integer, nullable=False)
    final_persona_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind_counts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    direct_trait_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    inferred_trait_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    unknown_trait_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    missing_trait_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    trait_support_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    geography_coverage_label: Mapped[str] = mapped_column(String(16), nullable=False)
    geography_coverage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_freshness_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    representativeness_caveats: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    missing_evidence_warnings: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    compliance_status: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    society_strength_label: Mapped[str] = mapped_column(String(16), nullable=False)
    society_strength_explanation: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    created_at: Mapped[CreatedAt]
