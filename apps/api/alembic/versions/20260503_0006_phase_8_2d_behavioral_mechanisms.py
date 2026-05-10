"""Phase 8.2D — behavioral science mechanism library.

Revision ID: 0006_phase_8_2d
Revises: 0005_phase_8_2c
Create Date: 2026-05-03

Why:
  Phase 8.2D adds a research-backed behavioral mechanism catalog that
  governs how Population-Mode personas will behave in Phase 8.2E+. The
  whole point of this layer is that:

    1. research papers shape mechanisms — mechanisms NEVER fabricate
       persona facts. Source evidence always outranks mechanism priors.
    2. demographic-only roleplay is a documented anti-pattern (see the
       belief-network research). The applicability rules table refuses to
       run pure demographic priors as if they were facts.
    3. cross-topic spillover is bounded by an explicit `belief_network_rules`
       table. The DB CHECK constraint on `allowed_inference_strength`
       FORBIDS the value `'strong'` — the strongest inference allowed by
       the framework is `'moderate'`. Spillover is a hint, not a fact.

  No external calls, no LLM calls, no persona writes happen in this
  migration or the package it ships with. This is a typed library +
  validators + seed catalog.

Tables (all additive — no destructive changes to existing tables):
  research_sources               — bibliographic registry
  behavioral_mechanisms          — closed-enum catalog of mechanisms
  mechanism_evidence_links       — bind each mechanism to its source(s)
  persuasion_strategy_taxonomy   — 14 strategies from Persuasion-for-Good
  belief_network_rules           — supported same-cluster spillover rules
  mechanism_applicability_rules  — domain × mechanism applicability hints
  mechanism_initialization_audit — required artifact when mechanisms are
                                    initialized for a persona (the audit
                                    panel reads this row)
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0006_phase_8_2d"
down_revision: str | None = "0005_phase_8_2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Closed enums — mirrored in pipeline/behavioral_science/constants.py.
_SOURCE_TYPES = (
    "uploaded_paper",
    "peer_reviewed_paper",
    "preprint",
    "dataset_paper",
    "internal_note",
    "other",
)
_MECH_CATEGORIES = (
    "persuasion",
    "opinion_change",
    "conformity",
    "belief_network",
    "memory",
    "planning",
    "social_influence",
    "simulation_bias",
    "population_sampling",
    "argument_style",
    "evidence_processing",
)
_MECH_STATUSES = ("active", "experimental", "deprecated")
_SUPPORT_TYPES = (
    "direct_claim",
    "empirical_result",
    "theoretical_support",
    "caution_or_limitation",
    "implementation_inspiration",
)
_RELATION_TYPES = ("same_cluster", "adjacent_cluster", "unrelated", "conflict")

# Allowed inference strengths. Note: 'strong' is DELIBERATELY EXCLUDED.
# The DB CHECK enforces strength ∈ {'none','weak','moderate'}.
_INFERENCE_STRENGTHS = ("none", "weak", "moderate")


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # ----------------------------------------------------------------------
    # 1) research_sources — bibliographic registry.
    # ----------------------------------------------------------------------
    op.create_table(
        "research_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("citation", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("title", name="uq_research_sources_title"),
        sa.CheckConstraint(
            f"source_type IN {_in_clause(_SOURCE_TYPES)}",
            name="ck_research_sources_source_type",
        ),
        sa.CheckConstraint(
            "year IS NULL OR (year >= 1900 AND year <= 2100)",
            name="ck_research_sources_year_range",
        ),
    )

    # ----------------------------------------------------------------------
    # 2) behavioral_mechanisms — closed-enum catalog.
    # ----------------------------------------------------------------------
    op.create_table(
        "behavioral_mechanisms",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, index=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("when_to_apply", sa.Text(), nullable=False),
        sa.Column("when_not_to_apply", sa.Text(), nullable=False),
        sa.Column(
            "default_strength",
            sa.Numeric(4, 2),
            nullable=False,
            server_default="0.5",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="uq_behavioral_mechanisms_name"),
        sa.CheckConstraint(
            f"category IN {_in_clause(_MECH_CATEGORIES)}",
            name="ck_behavioral_mechanisms_category",
        ),
        sa.CheckConstraint(
            f"status IN {_in_clause(_MECH_STATUSES)}",
            name="ck_behavioral_mechanisms_status",
        ),
        sa.CheckConstraint(
            "default_strength >= 0 AND default_strength <= 1",
            name="ck_behavioral_mechanisms_strength_range",
        ),
    )

    # ----------------------------------------------------------------------
    # 3) mechanism_evidence_links — every mechanism must trace to a source.
    # ----------------------------------------------------------------------
    op.create_table(
        "mechanism_evidence_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "mechanism_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("behavioral_mechanisms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "research_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("support_type", sa.String(32), nullable=False),
        sa.Column("excerpt_or_summary", sa.Text(), nullable=False),
        sa.Column("page_or_section", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "mechanism_id", "research_source_id", "support_type",
            name="uq_mechanism_evidence_links_unique",
        ),
        sa.CheckConstraint(
            f"support_type IN {_in_clause(_SUPPORT_TYPES)}",
            name="ck_mechanism_evidence_links_support_type",
        ),
    )

    # ----------------------------------------------------------------------
    # 4) persuasion_strategy_taxonomy — closed catalog of strategy names.
    # ----------------------------------------------------------------------
    op.create_table(
        "persuasion_strategy_taxonomy",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "research_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("usage_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "strategy_name", name="uq_persuasion_strategy_name",
        ),
    )

    # ----------------------------------------------------------------------
    # 5) belief_network_rules — bounded same-cluster spillover.
    # ----------------------------------------------------------------------
    op.create_table(
        "belief_network_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("topic_a", sa.String(80), nullable=False),
        sa.Column("topic_b", sa.String(80), nullable=False),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("allowed_inference_strength", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "research_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "topic_a", "topic_b", "relation_type",
            name="uq_belief_network_rules_pair",
        ),
        sa.CheckConstraint(
            "topic_a <> topic_b",
            name="ck_belief_network_rules_no_self_pair",
        ),
        sa.CheckConstraint(
            f"relation_type IN {_in_clause(_RELATION_TYPES)}",
            name="ck_belief_network_rules_relation_type",
        ),
        # CRITICAL: 'strong' is FORBIDDEN. The strongest belief-network
        # spillover allowed is 'moderate'. Any new rule attempting
        # strength='strong' is structurally rejected.
        sa.CheckConstraint(
            f"allowed_inference_strength IN {_in_clause(_INFERENCE_STRENGTHS)}",
            name="ck_belief_network_rules_strength_no_strong",
        ),
    )

    # ----------------------------------------------------------------------
    # 6) mechanism_applicability_rules — domain × mechanism hints.
    # ----------------------------------------------------------------------
    op.create_table(
        "mechanism_applicability_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "mechanism_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("behavioral_mechanisms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("domain_label", sa.String(64), nullable=False, index=True),
        sa.Column(
            "applies_when",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "research_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "mechanism_id", "domain_label",
            name="uq_mechanism_applicability_rules_mech_domain",
        ),
    )

    # ----------------------------------------------------------------------
    # 7) mechanism_initialization_audit — required per persona init run.
    # ----------------------------------------------------------------------
    op.create_table(
        "mechanism_initialization_audit",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "simulation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "applied_mechanisms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "skipped_mechanisms",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "applied_belief_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "anti_pattern_warnings",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "evidence_outranked_priors",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("mechanism_initialization_audit")
    op.drop_table("mechanism_applicability_rules")
    op.drop_table("belief_network_rules")
    op.drop_table("persuasion_strategy_taxonomy")
    op.drop_table("mechanism_evidence_links")
    op.drop_table("behavioral_mechanisms")
    op.drop_table("research_sources")
