"""Phase 9E — ORM models for the simulated intent + society-wide
debate layer. Mirrors alembic revision 0010_phase_9_e. Four additive
tables; no mutation of any 9A/9B/9D row.
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk


INTENT_LABELS: tuple[str, ...] = (
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
INTENT_STRENGTHS: tuple[str, ...] = ("low", "medium", "high")
SWITCHING_STATUSES: tuple[str, ...] = (
    "no_current_alternative",
    "weakly_attached_to_alternative",
    "actively_comparing",
    "loyal_to_current_alternative",
    "refuses_switching",
)
CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
STANCE_LABELS: tuple[str, ...] = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)
ARGUMENT_TYPES: tuple[str, ...] = (
    "objection", "proof_need", "persuasion_lever",
    "switching_trigger", "loyalist_resistance",
    "price_value", "trust_safety",
)
ARGUMENT_ORIGINS: tuple[str, ...] = (
    "cohort", "persona", "discussion_turn", "ballot",
)
RESPONSE_TYPES: tuple[str, ...] = (
    "adopted", "resisted", "modified", "ignored", "intensified",
)
INTENT_EFFECTS: tuple[str, ...] = (
    "increased_intent", "decreased_intent",
    "no_change", "increased_uncertainty",
)


class SimulatedIntent(Base):
    __tablename__ = "simulated_intents"
    __table_args__ = (
        UniqueConstraint(
            "run_scope_id", "persona_id",
            name="uq_simulated_intents_unique",
        ),
        CheckConstraint(
            "simulated_intent IN ('would_buy_now','would_try_once',"
            "'would_join_waitlist','would_consider_if_proven',"
            "'would_share_with_friend','would_compare_to_current_brand',"
            "'loyal_to_current_alternative','would_reject','would_block')",
            name="ck_simulated_intents_intent_label",
        ),
        CheckConstraint(
            "intent_strength IN ('low','medium','high')",
            name="ck_simulated_intents_strength",
        ),
        CheckConstraint(
            "switching_status IN ('no_current_alternative',"
            "'weakly_attached_to_alternative','actively_comparing',"
            "'loyal_to_current_alternative','refuses_switching')",
            name="ck_simulated_intents_switching",
        ),
        CheckConstraint(
            "confidence IN ('high','medium','low')",
            name="ck_simulated_intents_confidence",
        ),
        CheckConstraint(
            "stance_label IN ('curious_but_unconvinced',"
            "'interested_if_proven','skeptical','likely_reject',"
            "'needs_more_information')",
            name="ck_simulated_intents_stance",
        ),
        CheckConstraint(
            "char_length(evidence_basis) >= 1",
            name="ck_simulated_intents_evidence_basis_nonempty",
        ),
        CheckConstraint(
            "char_length(caveat) >= 1",
            name="ck_simulated_intents_caveat_nonempty",
        ),
    )

    id: Mapped[UUIDPk]
    run_scope_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    cohort_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("society_cohorts.id", ondelete="SET NULL"),
        nullable=True,
    )
    stance_label: Mapped[str] = mapped_column(String(48), nullable=False)
    simulated_intent: Mapped[str] = mapped_column(
        String(48), nullable=False,
    )
    intent_strength: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    switching_status: Mapped[str] = mapped_column(
        String(48), nullable=False,
    )
    current_alternative: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    conditions_to_buy: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    reason_for_rejection: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    proof_needed: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    evidence_basis: Mapped[str] = mapped_column(Text, nullable=False)
    discussion_turn_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    ballot_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    memory_atom_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    caveat: Mapped[str] = mapped_column(Text, nullable=False)
    generated_for_phase: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    created_at: Mapped[CreatedAt]


class SimulatedIntentRollup(Base):
    __tablename__ = "simulated_intent_rollups"
    __table_args__ = (
        UniqueConstraint(
            "run_scope_id", "phase",
            name="uq_simulated_intent_rollups_run_phase",
        ),
        CheckConstraint(
            "persona_count >= 1",
            name="ck_simulated_intent_rollups_persona_count",
        ),
        CheckConstraint(
            "cohort_count >= 1",
            name="ck_simulated_intent_rollups_cohort_count",
        ),
    )

    id: Mapped[UUIDPk]
    run_scope_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    persona_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cohort_count: Mapped[int] = mapped_column(Integer, nullable=False)
    intent_distribution: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    intent_by_cohort: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    switching_status_distribution: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    high_intent_segments: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    strongest_rejection_segments: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list,
    )
    caveats: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    quality_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    created_at: Mapped[CreatedAt]


class SocietyArgument(Base):
    __tablename__ = "society_arguments"
    __table_args__ = (
        CheckConstraint(
            "argument_type IN ('objection','proof_need','persuasion_lever',"
            "'switching_trigger','loyalist_resistance','price_value',"
            "'trust_safety')",
            name="ck_society_arguments_type",
        ),
        CheckConstraint(
            "origin_type IN ('cohort','persona','discussion_turn','ballot')",
            name="ck_society_arguments_origin_type",
        ),
        CheckConstraint(
            "char_length(argument_text) >= 1",
            name="ck_society_arguments_text_nonempty",
        ),
        Index(
            "ix_society_arguments_run_phase",
            "run_scope_id", "phase",
        ),
    )

    id: Mapped[UUIDPk]
    run_scope_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_ref_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True), nullable=False,
    )
    argument_text: Mapped[str] = mapped_column(Text, nullable=False)
    argument_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_cohort_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("society_cohorts.id", ondelete="SET NULL"),
        nullable=True,
    )
    supporting_turn_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    supporting_memory_atom_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    created_at: Mapped[CreatedAt]


class SocietyArgumentPropagation(Base):
    __tablename__ = "society_argument_propagation"
    __table_args__ = (
        UniqueConstraint(
            "argument_id", "target_cohort_id",
            name="uq_society_argument_propagation_unique",
        ),
        CheckConstraint(
            "response_type IN ('adopted','resisted','modified','ignored',"
            "'intensified')",
            name="ck_society_argument_propagation_response_type",
        ),
        CheckConstraint(
            "effect_on_intent IN ('increased_intent','decreased_intent',"
            "'no_change','increased_uncertainty')",
            name="ck_society_argument_propagation_effect",
        ),
        CheckConstraint(
            "char_length(response_text) >= 1",
            name="ck_society_argument_propagation_response_nonempty",
        ),
        CheckConstraint(
            "char_length(evidence_basis) >= 1",
            name="ck_society_argument_propagation_evidence_nonempty",
        ),
    )

    id: Mapped[UUIDPk]
    argument_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("society_arguments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    target_cohort_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("society_cohorts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    representative_persona_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    response_type: Mapped[str] = mapped_column(String(32), nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    effect_on_intent: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )
    evidence_basis: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[CreatedAt]
