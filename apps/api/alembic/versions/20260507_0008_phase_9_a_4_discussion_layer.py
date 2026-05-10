"""Phase 9A.4 — human-like discussion layer.

Revision ID: 0008_phase_9_a_4
Revises: 0007_phase_9_a_3
Create Date: 2026-05-07

Why:
  Phase 9A.4 introduces the discussion-layer infrastructure that lets
  the run-scoped 30-person society talk to each other, challenge each
  other, and either privately update or publicly comply. Five additive
  tables — no destructive changes.

Tables:
  discussion_sessions          — top-level run row, one per discussion
  discussion_groups            — N-persona subsets within a session
  discussion_turns             — public turns (with stance + references)
  discussion_private_ballots   — private pre/reflection/final ballots
  persona_memory_atoms         — source-grounded persona memory; each
                                  atom must cite a real origin row.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0008_phase_9_a_4"
down_revision: str | None = "0007_phase_9_a_3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SESSION_TYPES = (
    "six_round_v1", "pilot", "extended",
)
_SESSION_STATUSES = (
    "pending", "running", "completed", "failed",
)
_TURN_TYPES = (
    "public_opening",
    "challenge",
    "peer_response",
    "proof_discussion",
)
_BALLOT_STAGES = ("pre", "reflection", "final")
_DISCUSSION_STANCES = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)
_PUBLIC_PRIVATE_DELTAS = (
    "private_acceptance",
    "public_compliance_only",
    "resistance",
    "no_change",
    "polarization",
    "uncertainty_increase",
)
_CONFIDENCES = ("high", "medium", "low")
_MEMORY_TYPES = (
    "evidence",
    "trait",
    "psychology",
    "prior_simulation",
    "discussion_turn",
    "private_ballot",
)
_ORIGIN_TYPES = (
    "source_record",
    "persona_trait",
    "persona_psychology_trait",
    "persona_evidence_link",
    "agent_response",
    "discussion_turn",
    "discussion_private_ballot",
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # 1) discussion_sessions
    op.create_table(
        "discussion_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_scope_id", sa.String(64), nullable=False, index=True),
        sa.Column("product_name", sa.String(64), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("session_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"session_type IN {_in_clause(_SESSION_TYPES)}",
            name="ck_discussion_sessions_session_type",
        ),
        sa.CheckConstraint(
            f"status IN {_in_clause(_SESSION_STATUSES)}",
            name="ck_discussion_sessions_status",
        ),
    )

    # 2) discussion_groups
    op.create_table(
        "discussion_groups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "discussion_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discussion_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("group_index", sa.Integer(), nullable=False),
        sa.Column("group_strategy", sa.String(64), nullable=False),
        sa.Column(
            "persona_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "discussion_session_id", "group_index",
            name="uq_discussion_groups_session_index",
        ),
    )

    # 3) discussion_turns
    op.create_table(
        "discussion_turns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "discussion_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discussion_groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column(
            "speaker_persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("turn_type", sa.String(32), nullable=False),
        sa.Column("public_text", sa.Text(), nullable=False),
        sa.Column("stance", sa.String(32), nullable=True),
        sa.Column(
            "referenced_turn_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "referenced_source_record_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "referenced_memory_atom_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "psychology_control_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "forbidden_claim_audit",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "discussion_group_id", "round_number", "turn_number",
            name="uq_discussion_turns_group_round_turn",
        ),
        sa.CheckConstraint(
            f"turn_type IN {_in_clause(_TURN_TYPES)}",
            name="ck_discussion_turns_turn_type",
        ),
        sa.CheckConstraint(
            f"stance IS NULL OR stance IN {_in_clause(_DISCUSSION_STANCES)}",
            name="ck_discussion_turns_stance",
        ),
    )
    op.create_index(
        "ix_discussion_turns_group_round",
        "discussion_turns",
        ["discussion_group_id", "round_number", "turn_number"],
    )

    # 4) discussion_private_ballots
    op.create_table(
        "discussion_private_ballots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "discussion_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discussion_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "discussion_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discussion_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("ballot_stage", sa.String(32), nullable=False),
        sa.Column("private_stance", sa.String(32), nullable=False),
        sa.Column("private_reasoning", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column(
            "public_private_delta", sa.String(32), nullable=True,
        ),
        sa.Column("top_objection", sa.Text(), nullable=True),
        sa.Column("top_proof_need", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "discussion_session_id", "persona_id", "ballot_stage",
            name="uq_discussion_private_ballots_unique",
        ),
        sa.CheckConstraint(
            f"ballot_stage IN {_in_clause(_BALLOT_STAGES)}",
            name="ck_discussion_private_ballots_stage",
        ),
        sa.CheckConstraint(
            f"private_stance IN {_in_clause(_DISCUSSION_STANCES)}",
            name="ck_discussion_private_ballots_stance",
        ),
        sa.CheckConstraint(
            f"confidence IN {_in_clause(_CONFIDENCES)}",
            name="ck_discussion_private_ballots_confidence",
        ),
        sa.CheckConstraint(
            "public_private_delta IS NULL OR public_private_delta IN "
            f"{_in_clause(_PUBLIC_PRIVATE_DELTAS)}",
            name="ck_discussion_private_ballots_delta",
        ),
    )

    # 5) persona_memory_atoms
    op.create_table(
        "persona_memory_atoms",
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
            nullable=False,
            index=True,
        ),
        sa.Column("run_scope_id", sa.String(64), nullable=False, index=True),
        sa.Column("memory_type", sa.String(32), nullable=False),
        sa.Column("origin_type", sa.String(32), nullable=False),
        sa.Column(
            "origin_ref_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("origin_excerpt", sa.Text(), nullable=False),
        sa.Column("memory_text", sa.Text(), nullable=False),
        sa.Column("importance_score", sa.Integer(), nullable=False),
        sa.Column("recency_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "relevance_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "invalidated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_memory_atoms.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"memory_type IN {_in_clause(_MEMORY_TYPES)}",
            name="ck_persona_memory_atoms_memory_type",
        ),
        sa.CheckConstraint(
            f"origin_type IN {_in_clause(_ORIGIN_TYPES)}",
            name="ck_persona_memory_atoms_origin_type",
        ),
        sa.CheckConstraint(
            "importance_score >= 1 AND importance_score <= 10",
            name="ck_persona_memory_atoms_importance_range",
        ),
        sa.CheckConstraint(
            "char_length(origin_excerpt) >= 1",
            name="ck_persona_memory_atoms_origin_excerpt_nonempty",
        ),
    )
    op.create_index(
        "ix_persona_memory_atoms_persona_run",
        "persona_memory_atoms",
        ["persona_id", "run_scope_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_persona_memory_atoms_persona_run",
        table_name="persona_memory_atoms",
    )
    op.drop_table("persona_memory_atoms")
    op.drop_table("discussion_private_ballots")
    op.drop_index(
        "ix_discussion_turns_group_round",
        table_name="discussion_turns",
    )
    op.drop_table("discussion_turns")
    op.drop_table("discussion_groups")
    op.drop_table("discussion_sessions")
