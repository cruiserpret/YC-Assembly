"""Phase 9E — simulated intent layer + fast society-wide debate.

Revision ID: 0010_phase_9_e
Revises: 0009_phase_9_d
Create Date: 2026-05-08

Why:
  Phase 9E adds a per-persona simulated-intent layer (separate from the
  cautious stance label) and a cohort-representative argument
  propagation model. Four additive tables — no destructive changes.

Tables:
  simulated_intents            — one intent record per persona per
                                  run scope
  simulated_intent_rollups     — one rollup row per (run_scope, phase)
  society_arguments            — distinct arguments discovered from
                                  the 9B/9D society
  society_argument_propagation — one record per (argument, target
                                  cohort) — adopted / resisted /
                                  modified / ignored / intensified
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010_phase_9_e"
down_revision: str | None = "0009_phase_9_d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INTENT_LABELS = (
    "would_buy_now",
    "would_try_once",
    "would_join_waitlist",
    "would_consider_if_proven",
    "would_share_with_friend",
    "would_compare_to_current_brand",
    "loyal_to_current_alternative",
    "would_reject",
    "would_block",
)
_INTENT_STRENGTHS = ("low", "medium", "high")
_SWITCHING_STATUSES = (
    "no_current_alternative",
    "weakly_attached_to_alternative",
    "actively_comparing",
    "loyal_to_current_alternative",
    "refuses_switching",
)
_CONFIDENCES = ("high", "medium", "low")
_STANCE_LABELS = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)
_ARGUMENT_TYPES = (
    "objection", "proof_need", "persuasion_lever",
    "switching_trigger", "loyalist_resistance",
    "price_value", "trust_safety",
)
_ARGUMENT_ORIGINS = (
    "cohort", "persona", "discussion_turn", "ballot",
)
_RESPONSE_TYPES = (
    "adopted", "resisted", "modified", "ignored", "intensified",
)
_EFFECTS = (
    "increased_intent", "decreased_intent",
    "no_change", "increased_uncertainty",
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # 1) simulated_intents
    op.create_table(
        "simulated_intents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_scope_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("society_cohorts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("stance_label", sa.String(48), nullable=False),
        sa.Column("simulated_intent", sa.String(48), nullable=False),
        sa.Column("intent_strength", sa.String(16), nullable=False),
        sa.Column("switching_status", sa.String(48), nullable=False),
        sa.Column(
            "current_alternative", sa.String(255), nullable=True,
        ),
        sa.Column(
            "conditions_to_buy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "reason_for_rejection", sa.Text(), nullable=True,
        ),
        sa.Column(
            "proof_needed",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("evidence_basis", sa.Text(), nullable=False),
        sa.Column(
            "discussion_turn_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "ballot_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "memory_atom_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("caveat", sa.Text(), nullable=False),
        sa.Column("generated_for_phase", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "run_scope_id", "persona_id",
            name="uq_simulated_intents_unique",
        ),
        sa.CheckConstraint(
            f"simulated_intent IN {_in_clause(_INTENT_LABELS)}",
            name="ck_simulated_intents_intent_label",
        ),
        sa.CheckConstraint(
            f"intent_strength IN {_in_clause(_INTENT_STRENGTHS)}",
            name="ck_simulated_intents_strength",
        ),
        sa.CheckConstraint(
            f"switching_status IN {_in_clause(_SWITCHING_STATUSES)}",
            name="ck_simulated_intents_switching",
        ),
        sa.CheckConstraint(
            f"confidence IN {_in_clause(_CONFIDENCES)}",
            name="ck_simulated_intents_confidence",
        ),
        sa.CheckConstraint(
            f"stance_label IN {_in_clause(_STANCE_LABELS)}",
            name="ck_simulated_intents_stance",
        ),
        sa.CheckConstraint(
            "char_length(evidence_basis) >= 1",
            name="ck_simulated_intents_evidence_basis_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(caveat) >= 1",
            name="ck_simulated_intents_caveat_nonempty",
        ),
    )

    # 2) simulated_intent_rollups
    op.create_table(
        "simulated_intent_rollups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_scope_id", sa.String(64), nullable=False, index=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("persona_count", sa.Integer(), nullable=False),
        sa.Column("cohort_count", sa.Integer(), nullable=False),
        sa.Column(
            "intent_distribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "intent_by_cohort",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "switching_status_distribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "high_intent_segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "strongest_rejection_segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "caveats",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::text[]"),
        ),
        sa.Column(
            "quality_scores",
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
            "run_scope_id", "phase",
            name="uq_simulated_intent_rollups_run_phase",
        ),
        sa.CheckConstraint(
            "persona_count >= 1",
            name="ck_simulated_intent_rollups_persona_count",
        ),
        sa.CheckConstraint(
            "cohort_count >= 1",
            name="ck_simulated_intent_rollups_cohort_count",
        ),
    )

    # 3) society_arguments
    op.create_table(
        "society_arguments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_scope_id", sa.String(64), nullable=False, index=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("origin_type", sa.String(32), nullable=False),
        sa.Column(
            "origin_ref_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("argument_text", sa.Text(), nullable=False),
        sa.Column("argument_type", sa.String(32), nullable=False),
        sa.Column(
            "source_cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("society_cohorts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "supporting_turn_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "supporting_memory_atom_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"argument_type IN {_in_clause(_ARGUMENT_TYPES)}",
            name="ck_society_arguments_type",
        ),
        sa.CheckConstraint(
            f"origin_type IN {_in_clause(_ARGUMENT_ORIGINS)}",
            name="ck_society_arguments_origin_type",
        ),
        sa.CheckConstraint(
            "char_length(argument_text) >= 1",
            name="ck_society_arguments_text_nonempty",
        ),
    )
    op.create_index(
        "ix_society_arguments_run_phase",
        "society_arguments",
        ["run_scope_id", "phase"],
    )

    # 4) society_argument_propagation
    op.create_table(
        "society_argument_propagation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "argument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("society_arguments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("society_cohorts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "representative_persona_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persona_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("response_type", sa.String(32), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("effect_on_intent", sa.String(32), nullable=False),
        sa.Column("evidence_basis", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "argument_id", "target_cohort_id",
            name="uq_society_argument_propagation_unique",
        ),
        sa.CheckConstraint(
            f"response_type IN {_in_clause(_RESPONSE_TYPES)}",
            name="ck_society_argument_propagation_response_type",
        ),
        sa.CheckConstraint(
            f"effect_on_intent IN {_in_clause(_EFFECTS)}",
            name="ck_society_argument_propagation_effect",
        ),
        sa.CheckConstraint(
            "char_length(response_text) >= 1",
            name="ck_society_argument_propagation_response_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(evidence_basis) >= 1",
            name="ck_society_argument_propagation_evidence_nonempty",
        ),
    )


def downgrade() -> None:
    op.drop_table("society_argument_propagation")
    op.drop_index(
        "ix_society_arguments_run_phase",
        table_name="society_arguments",
    )
    op.drop_table("society_arguments")
    op.drop_table("simulated_intent_rollups")
    op.drop_table("simulated_intents")
