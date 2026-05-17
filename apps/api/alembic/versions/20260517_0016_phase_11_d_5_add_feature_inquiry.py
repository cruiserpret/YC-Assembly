"""Phase 11D.5 — add feature_inquiry to tech_market_signal.signal_type.

Revision ID: 0016_phase_11_d_5
Revises: 0015_phase_11_d_1
Create Date: 2026-05-17

Why:
  Phase 11D.3's first real Product Hunt dry-run revealed that 6 of 9
  rejected rows were pure feature-inquiry questions ("Can I use my
  own character designs?", "How does it handle brand logos?",
  "How long does generation take?"). These are valuable demand
  signals that the current 14-value controlled vocabulary cannot
  represent. Phase 11D.5 introduces a 15th signal_type:
  `feature_inquiry`.

  The change is additive — no existing rows are altered. We only
  widen the CHECK constraint to allow one more value. Downgrade is
  clean as long as no rows have been written with the new value
  (Phase 11D.1-style: the table still ships empty in production
  because the runtime flag remains off).

Schema impact:
  * Drop CHECK constraint `ck_tech_market_signal_signal_type`.
  * Recreate it with the new 15-value set.
  * Downgrade: drop and recreate with the old 14-value set, AFTER
    refusing to run if any rows with `signal_type='feature_inquiry'`
    exist (would orphan them under the old CHECK).
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0016_phase_11_d_5"
down_revision: str | None = "0015_phase_11_d_1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep in lockstep with:
#   * assembly.sources.tech_market_provider.signal_types.SIGNAL_TYPES
#   * assembly.models.tech_market_signal.SIGNAL_TYPES
# Drift-tested in `tests/test_tech_market_feature_inquiry_11d_5.py`.
_SIGNAL_TYPES_V2 = (
    "pain_urgency",
    "switching_objection",
    "pricing_objection",
    "trust_security_concern",
    "integration_friction",
    "onboarding_friction",
    "support_complaint",
    "competitor_comparison",
    "willingness_to_pay",
    "nice_to_have_risk",
    "feature_not_company_risk",
    "workflow_fit",
    "developer_skepticism",
    "procurement_friction",
    "feature_inquiry",  # new in 11D.5
)


_SIGNAL_TYPES_V1 = tuple(
    v for v in _SIGNAL_TYPES_V2 if v != "feature_inquiry"
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # Drop the Phase 11D.1 CHECK constraint and recreate it with the
    # new 15-value set.
    op.drop_constraint(
        "ck_tech_market_signal_signal_type",
        "tech_market_signal",
        type_="check",
    )
    op.create_check_constraint(
        "ck_tech_market_signal_signal_type",
        "tech_market_signal",
        f"signal_type IN {_in_clause(_SIGNAL_TYPES_V2)}",
    )


def downgrade() -> None:
    # Refuse to downgrade if any rows are stamped with the new value
    # — would silently fail the recreated CHECK on subsequent writes
    # and orphan the existing rows under the old constraint shape.
    bind = op.get_bind()
    res = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM tech_market_signal "
            "WHERE signal_type = 'feature_inquiry'",
        ),
    )
    count = res.scalar()
    if count and int(count) > 0:
        raise RuntimeError(
            f"refusing to downgrade Phase 11D.5: "
            f"{count} tech_market_signal rows have "
            f"signal_type='feature_inquiry'. Reclassify or delete "
            f"those rows first, then re-run downgrade.",
        )
    op.drop_constraint(
        "ck_tech_market_signal_signal_type",
        "tech_market_signal",
        type_="check",
    )
    op.create_check_constraint(
        "ck_tech_market_signal_signal_type",
        "tech_market_signal",
        f"signal_type IN {_in_clause(_SIGNAL_TYPES_V1)}",
    )
