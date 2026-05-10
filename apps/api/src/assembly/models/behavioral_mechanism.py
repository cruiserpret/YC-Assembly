"""Phase 8.2D — ORM models for the behavioral science mechanism library.

Schema-only contract:
  - 7 tables (research_sources, behavioral_mechanisms, mechanism_evidence_links,
    persuasion_strategy_taxonomy, belief_network_rules,
    mechanism_applicability_rules, mechanism_initialization_audit).
  - All closed enums mirrored from the migration's CHECK constraints AND
    `pipeline/behavioral_science/constants.py`.
  - belief_network_rules.allowed_inference_strength FORBIDS the value
    'strong' at the DB layer — the strongest spillover the framework
    allows is 'moderate'. Mechanism priors NEVER outrank source evidence.

This module imports nothing from `pipeline/behavioral_science/`; the
package depends on this module, not the other way around.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class ResearchSource(Base):
    """One row per research artifact (paper, preprint, dataset, internal note).

    Phase 8.2D seed loader inserts the 8 themed sources from the user's
    behavioral-science theme list. No external fetch — bibliographic only.
    """

    __tablename__ = "research_sources"
    __table_args__ = (
        UniqueConstraint("title", name="uq_research_sources_title"),
        CheckConstraint(
            "source_type IN ('uploaded_paper','peer_reviewed_paper',"
            "'preprint','dataset_paper','internal_note','other')",
            name="ck_research_sources_source_type",
        ),
        CheckConstraint(
            "year IS NULL OR (year >= 1900 AND year <= 2100)",
            name="ck_research_sources_year_range",
        ),
    )

    id: Mapped[UUIDPk]
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]


class BehavioralMechanism(Base):
    """A single, named behavioral mechanism in the catalog.

    Mechanisms are research-backed priors — NOT facts. They guide future
    persona initialization and simulation behavior. Mechanism strength
    NEVER outranks bound source evidence; the validator checks this.
    """

    __tablename__ = "behavioral_mechanisms"
    __table_args__ = (
        UniqueConstraint("name", name="uq_behavioral_mechanisms_name"),
        CheckConstraint(
            "category IN ('persuasion','opinion_change','conformity',"
            "'belief_network','memory','planning','social_influence',"
            "'simulation_bias','population_sampling','argument_style',"
            "'evidence_processing')",
            name="ck_behavioral_mechanisms_category",
        ),
        CheckConstraint(
            "status IN ('active','experimental','deprecated')",
            name="ck_behavioral_mechanisms_status",
        ),
        CheckConstraint(
            "default_strength >= 0 AND default_strength <= 1",
            name="ck_behavioral_mechanisms_strength_range",
        ),
    )

    id: Mapped[UUIDPk]
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    when_to_apply: Mapped[str] = mapped_column(Text, nullable=False)
    when_not_to_apply: Mapped[str] = mapped_column(Text, nullable=False)
    default_strength: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("0.5"),
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active",
    )
    created_at: Mapped[CreatedAt]


class MechanismEvidenceLink(Base):
    """Bind a mechanism to the research source(s) that justify it.

    No mechanism may exist without at least one supporting evidence link
    (the validator enforces this — the DB FK cascade does the rest).
    """

    __tablename__ = "mechanism_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "mechanism_id", "research_source_id", "support_type",
            name="uq_mechanism_evidence_links_unique",
        ),
        CheckConstraint(
            "support_type IN ('direct_claim','empirical_result',"
            "'theoretical_support','caution_or_limitation',"
            "'implementation_inspiration')",
            name="ck_mechanism_evidence_links_support_type",
        ),
    )

    id: Mapped[UUIDPk]
    mechanism_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("behavioral_mechanisms.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    research_source_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("research_sources.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    support_type: Mapped[str] = mapped_column(String(32), nullable=False)
    excerpt_or_summary: Mapped[str] = mapped_column(Text, nullable=False)
    page_or_section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[CreatedAt]


class PersuasionStrategyTaxonomy(Base):
    """Closed catalog of persuasion strategy names.

    Phase 8.2D seeds 14 strategies derived from the Persuasion-for-Good
    research theme: logical_appeal, emotional_appeal, credibility_appeal,
    personal_story, self_modeling, foot_in_the_door,
    task_product_information, source_related_inquiry,
    task_related_inquiry, personal_related_inquiry, evidence_linking,
    social_proof, authority_signal, peer_conformity_signal.
    """

    __tablename__ = "persuasion_strategy_taxonomy"
    __table_args__ = (
        UniqueConstraint(
            "strategy_name", name="uq_persuasion_strategy_name",
        ),
    )

    id: Mapped[UUIDPk]
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    research_source_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("research_sources.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    usage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]


class BeliefNetworkRule(Base):
    """Bounded same-cluster spillover between two topics.

    The DB CHECK constraint REJECTS `allowed_inference_strength='strong'`
    — only `'none'`, `'weak'`, `'moderate'` are accepted. Spillover is a
    hint, not a fact. Source evidence always outranks belief priors.
    """

    __tablename__ = "belief_network_rules"
    __table_args__ = (
        UniqueConstraint(
            "topic_a", "topic_b", "relation_type",
            name="uq_belief_network_rules_pair",
        ),
        CheckConstraint(
            "topic_a <> topic_b",
            name="ck_belief_network_rules_no_self_pair",
        ),
        CheckConstraint(
            "relation_type IN ('same_cluster','adjacent_cluster',"
            "'unrelated','conflict')",
            name="ck_belief_network_rules_relation_type",
        ),
        CheckConstraint(
            "allowed_inference_strength IN ('none','weak','moderate')",
            name="ck_belief_network_rules_strength_no_strong",
        ),
    )

    id: Mapped[UUIDPk]
    topic_a: Mapped[str] = mapped_column(String(80), nullable=False)
    topic_b: Mapped[str] = mapped_column(String(80), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_inference_strength: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_source_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("research_sources.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    created_at: Mapped[CreatedAt]


class MechanismApplicabilityRule(Base):
    """Domain × mechanism applicability hint.

    `domain_label` is a coarse domain tag (e.g. `commerce`,
    `political_opinion`, `health`, `unsupported_demographic_only`). The
    `applies_when` JSONB carries the structured precondition payload.
    """

    __tablename__ = "mechanism_applicability_rules"
    __table_args__ = (
        UniqueConstraint(
            "mechanism_id", "domain_label",
            name="uq_mechanism_applicability_rules_mech_domain",
        ),
    )

    id: Mapped[UUIDPk]
    mechanism_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("behavioral_mechanisms.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    domain_label: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    applies_when: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_source_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("research_sources.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    created_at: Mapped[CreatedAt]


class MechanismInitializationAudit(Base):
    """REQUIRED audit row whenever mechanisms are initialized for a persona.

    Captures which mechanisms were applied, which were skipped (e.g.
    refused due to anti-pattern checks), which belief-network rules were
    consulted, and whether bound source evidence outranked any conflicting
    mechanism prior. The audit panel surfaces this row in Phase 8.2H+.

    Phase 8.2D ships the schema; the package's `audit.py` writes rows.
    `persona_id` and `simulation_id` are both nullable because the
    initializer can be exercised in unit tests without a persona/sim row.
    """

    __tablename__ = "mechanism_initialization_audit"

    id: Mapped[UUIDPk]
    persona_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    simulation_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("simulations.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    applied_mechanisms: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    skipped_mechanisms: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    applied_belief_rules: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    anti_pattern_warnings: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    evidence_outranked_priors: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]
