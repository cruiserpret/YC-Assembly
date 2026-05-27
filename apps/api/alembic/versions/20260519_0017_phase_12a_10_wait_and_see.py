"""Phase 12A.10 — add `wait_and_see` to simulated_intent CHECK constraint.

Revision ID: 0017_phase_12a_10
Revises: 0016_phase_11_d_5
Create Date: 2026-05-19

Why:
  Phase 12A.9 produced Assembly's first real blind calibration score
  against the Opslane Show-HN outcome. The result (MAE 9.40pp) was
  entirely driven by ONE structural artifact in the intent rule
  cascade: ambiguous personas with stance
  `curious_but_unconvinced` / `needs_more_information` and no
  positive adoption tokens were ALL routed to
  `would_consider_if_proven` (which maps to the `receptive`
  calibration bucket). Real-world HN comments showed those
  personas were actually `uncertain`. Result: receptive +18pp,
  uncertain -18pp.

  Phase 12A.10 fixes this at the cascade level by introducing a
  new intent label `wait_and_see` that maps to `uncertain` via
  the calibration bucket vocabulary (already present in
  `assembly.calibration.market_buckets.ASSEMBLY_LABEL_TO_BUCKET`).
  The DB CHECK constraint on `simulated_intents.simulated_intent`
  must be widened to accept the new value, otherwise live
  founder-brief runs will fail to persist intent rows.

Change is additive — no existing rows are altered.

Schema impact:
  * Drop CHECK constraint `ck_simulated_intents_intent_label`.
  * Recreate it with the 10-value set (9 prior + `wait_and_see`).
  * Downgrade: refuse if any rows are stamped with `wait_and_see`
    (would orphan rows under the old constraint shape), else
    revert to the 9-value set.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017_phase_12a_10"
down_revision: str | None = "0016_phase_11_d_5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep in lockstep with:
#   * assembly.sources.intent_layer.schemas.IntentLabel
#   * assembly.models.intent.INTENT_LABELS
# Drift-tested in test_intent_cascade_wait_and_see_12a_10.py.
_INTENT_LABELS_V2 = (
    "would_buy_now",
    "would_try_once",
    "would_join_waitlist",
    "would_consider_if_proven",
    "would_share_with_friend",
    "would_compare_to_current_brand",
    "loyal_to_current_alternative",
    "would_reject",
    "would_block",
    "wait_and_see",  # new in 12A.10
)


_INTENT_LABELS_V1 = tuple(
    v for v in _INTENT_LABELS_V2 if v != "wait_and_see"
)


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_simulated_intents_intent_label",
        "simulated_intents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_simulated_intents_intent_label",
        "simulated_intents",
        f"simulated_intent IN {_in_clause(_INTENT_LABELS_V2)}",
    )


def downgrade() -> None:
    bind = op.get_bind()
    res = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM simulated_intents "
            "WHERE simulated_intent = 'wait_and_see'",
        ),
    )
    count = res.scalar()
    if count and int(count) > 0:
        raise RuntimeError(
            f"downgrade refused: {int(count)} simulated_intents row(s) "
            "are stamped 'wait_and_see'. Delete or re-classify them "
            "before downgrading the constraint."
        )
    op.drop_constraint(
        "ck_simulated_intents_intent_label",
        "simulated_intents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_simulated_intents_intent_label",
        "simulated_intents",
        f"simulated_intent IN {_in_clause(_INTENT_LABELS_V1)}",
    )
