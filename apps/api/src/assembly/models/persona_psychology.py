"""Phase 9A.3 — ORM model for the persona psychology layer.

Mirrors the schema introduced by alembic revision 0007_phase_9_a_3.
Additive only; no changes to PersonaRecord, PersonaTrait, PersonaEvidenceLink,
or SourceRecord.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk

if TYPE_CHECKING:
    pass


PSYCHOLOGY_TRAIT_NAMES: tuple[str, ...] = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
    "risk_tolerance",
    "novelty_seeking",
    "trust_proof_threshold",
    "social_influence_susceptibility",
    "category_involvement_or_expertise",
    "price_sensitivity",
)
OCEAN_TRAIT_NAMES: tuple[str, ...] = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
)
ADDITIONAL_TRAIT_NAMES: tuple[str, ...] = (
    "risk_tolerance",
    "novelty_seeking",
    "trust_proof_threshold",
    "social_influence_susceptibility",
    "category_involvement_or_expertise",
    "price_sensitivity",
)
VALUE_LABELS: tuple[str, ...] = ("low", "medium", "high")
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")
INFERENCE_METHODS: tuple[str, ...] = (
    "evidence_direct",
    "simulation_behavior",
    "role_context_prior",
    "neutral_default",
)


class PersonaPsychologyTrait(Base):
    """One inferred psychology trait for one persona, scoped to one run.

    Closed-set enums + DB CHECKs make it impossible to persist an unknown
    trait_name, an out-of-range value_numeric, or an inference_method that
    the validator was not built for. Either evidence_basis must be present,
    or inference_method must equal 'neutral_default' AND caveat must be
    present — enforced as a DB CHECK so unsupported priors cannot leak in.
    """

    __tablename__ = "persona_psychology_traits"
    __table_args__ = (
        UniqueConstraint(
            "persona_id", "trait_name", "run_scope_id",
            name="uq_persona_psychology_traits_unique",
        ),
        CheckConstraint(
            "value_numeric >= 0.0 AND value_numeric <= 1.0",
            name="ck_persona_psychology_traits_value_range",
        ),
        CheckConstraint(
            "trait_name IN ("
            "'openness','conscientiousness','extraversion','agreeableness',"
            "'neuroticism','risk_tolerance','novelty_seeking',"
            "'trust_proof_threshold','social_influence_susceptibility',"
            "'category_involvement_or_expertise','price_sensitivity')",
            name="ck_persona_psychology_traits_trait_name",
        ),
        CheckConstraint(
            "value_label IN ('low','medium','high')",
            name="ck_persona_psychology_traits_value_label",
        ),
        CheckConstraint(
            "confidence IN ('high','medium','low')",
            name="ck_persona_psychology_traits_confidence",
        ),
        CheckConstraint(
            "inference_method IN ('evidence_direct','simulation_behavior',"
            "'role_context_prior','neutral_default')",
            name="ck_persona_psychology_traits_inference_method",
        ),
        CheckConstraint(
            "(inference_method = 'neutral_default' AND caveat IS NOT NULL)"
            " OR (inference_method <> 'neutral_default'"
            "     AND evidence_basis IS NOT NULL)",
            name="ck_persona_psychology_traits_basis_or_caveat",
        ),
        Index(
            "ix_persona_psychology_traits_persona_run",
            "persona_id", "run_scope_id",
        ),
        Index(
            "ix_persona_psychology_traits_run_trait",
            "run_scope_id", "trait_name",
        ),
    )

    id: Mapped[UUIDPk]
    persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    run_scope_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    trait_name: Mapped[str] = mapped_column(String(64), nullable=False)
    value_numeric: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False,
    )
    value_label: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    inference_method: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_record_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    source_trait_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    simulation_response_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    caveat: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_for_phase: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    created_at: Mapped[CreatedAt]
