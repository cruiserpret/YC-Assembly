"""Phase 9A.3 — persona psychology layer.

Revision ID: 0007_phase_9_a_3
Revises: 0006_phase_8_2d
Create Date: 2026-05-07

Why:
  Phase 9A.3 enriches the run-scoped 9A.2 personas with an evidence-anchored
  human-psychology layer (OCEAN + 5 additional psychology traits). This
  table is additive only. It does not mutate persona_records, persona_traits,
  persona_evidence_links, or source_records. It is the foundation for the
  Phase 9A.4 discussion layer.

Tables added:
  persona_psychology_traits — per-(persona, trait_name, run_scope_id) row;
                              evidence-traceable values with closed enums.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0007_phase_9_a_3"
down_revision: str | None = "0006_phase_8_2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PSYCHOLOGY_TRAIT_NAMES = (
    # OCEAN
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
    # Additional psychology traits
    "risk_tolerance",
    "novelty_seeking",
    "trust_proof_threshold",
    "social_influence_susceptibility",
    "category_involvement_or_expertise",
    "price_sensitivity",
)
_VALUE_LABELS = ("low", "medium", "high")
_CONFIDENCES = ("high", "medium", "low")
_INFERENCE_METHODS = (
    "evidence_direct",
    "simulation_behavior",
    "role_context_prior",
    "neutral_default",
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "persona_psychology_traits",
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
        sa.Column("trait_name", sa.String(64), nullable=False),
        sa.Column("value_numeric", sa.Numeric(4, 3), nullable=False),
        sa.Column("value_label", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("inference_method", sa.String(32), nullable=False),
        sa.Column("evidence_basis", sa.Text(), nullable=True),
        sa.Column(
            "source_record_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "source_trait_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "simulation_response_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column("caveat", sa.Text(), nullable=True),
        sa.Column("generated_for_phase", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "persona_id", "trait_name", "run_scope_id",
            name="uq_persona_psychology_traits_unique",
        ),
        sa.CheckConstraint(
            "value_numeric >= 0.0 AND value_numeric <= 1.0",
            name="ck_persona_psychology_traits_value_range",
        ),
        sa.CheckConstraint(
            f"trait_name IN {_in_clause(_PSYCHOLOGY_TRAIT_NAMES)}",
            name="ck_persona_psychology_traits_trait_name",
        ),
        sa.CheckConstraint(
            f"value_label IN {_in_clause(_VALUE_LABELS)}",
            name="ck_persona_psychology_traits_value_label",
        ),
        sa.CheckConstraint(
            f"confidence IN {_in_clause(_CONFIDENCES)}",
            name="ck_persona_psychology_traits_confidence",
        ),
        sa.CheckConstraint(
            f"inference_method IN {_in_clause(_INFERENCE_METHODS)}",
            name="ck_persona_psychology_traits_inference_method",
        ),
        sa.CheckConstraint(
            "(inference_method = 'neutral_default' AND caveat IS NOT NULL)"
            " OR (inference_method <> 'neutral_default'"
            "     AND evidence_basis IS NOT NULL)",
            name="ck_persona_psychology_traits_basis_or_caveat",
        ),
    )
    op.create_index(
        "ix_persona_psychology_traits_persona_run",
        "persona_psychology_traits",
        ["persona_id", "run_scope_id"],
    )
    op.create_index(
        "ix_persona_psychology_traits_run_trait",
        "persona_psychology_traits",
        ["run_scope_id", "trait_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_persona_psychology_traits_run_trait",
        table_name="persona_psychology_traits",
    )
    op.drop_index(
        "ix_persona_psychology_traits_persona_run",
        table_name="persona_psychology_traits",
    )
    op.drop_table("persona_psychology_traits")
