"""Phase 9A.4 — ORM models for the human-like discussion layer.

Mirrors the schema introduced by alembic revision 0008_phase_9_a_4. All
five tables are additive — they do not mutate any 9A.2/9A.3 row.
"""
from __future__ import annotations

from datetime import datetime
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


SESSION_TYPES: tuple[str, ...] = ("six_round_v1", "pilot", "extended")
SESSION_STATUSES: tuple[str, ...] = (
    "pending", "running", "completed", "failed",
)
TURN_TYPES: tuple[str, ...] = (
    "public_opening", "challenge", "peer_response", "proof_discussion",
)
BALLOT_STAGES: tuple[str, ...] = ("pre", "reflection", "final")
DISCUSSION_STANCES: tuple[str, ...] = (
    "curious_but_unconvinced",
    "interested_if_proven",
    "skeptical",
    "likely_reject",
    "needs_more_information",
)
PUBLIC_PRIVATE_DELTAS: tuple[str, ...] = (
    "private_acceptance",
    "public_compliance_only",
    "resistance",
    "no_change",
    "polarization",
    "uncertainty_increase",
)
MEMORY_TYPES: tuple[str, ...] = (
    "evidence", "trait", "psychology",
    "prior_simulation", "discussion_turn", "private_ballot",
)
ORIGIN_TYPES: tuple[str, ...] = (
    "source_record", "persona_trait", "persona_psychology_trait",
    "persona_evidence_link", "agent_response",
    "discussion_turn", "discussion_private_ballot",
)


class DiscussionSession(Base):
    __tablename__ = "discussion_sessions"
    __table_args__ = (
        CheckConstraint(
            "session_type IN ('six_round_v1','pilot','extended')",
            name="ck_discussion_sessions_session_type",
        ),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name="ck_discussion_sessions_status",
        ),
    )

    id: Mapped[UUIDPk]
    run_scope_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
    )
    product_name: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    session_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict,
    )
    created_at: Mapped[CreatedAt]


class DiscussionGroup(Base):
    __tablename__ = "discussion_groups"
    __table_args__ = (
        UniqueConstraint(
            "discussion_session_id", "group_index",
            name="uq_discussion_groups_session_index",
        ),
    )

    id: Mapped[UUIDPk]
    discussion_session_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("discussion_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    group_index: Mapped[int] = mapped_column(Integer, nullable=False)
    group_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    persona_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False,
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict,
    )
    created_at: Mapped[CreatedAt]


class DiscussionTurn(Base):
    __tablename__ = "discussion_turns"
    __table_args__ = (
        UniqueConstraint(
            "discussion_group_id", "round_number", "turn_number",
            name="uq_discussion_turns_group_round_turn",
        ),
        CheckConstraint(
            "turn_type IN ('public_opening','challenge','peer_response',"
            "'proof_discussion')",
            name="ck_discussion_turns_turn_type",
        ),
        CheckConstraint(
            "stance IS NULL OR stance IN ('curious_but_unconvinced',"
            "'interested_if_proven','skeptical','likely_reject',"
            "'needs_more_information')",
            name="ck_discussion_turns_stance",
        ),
        Index(
            "ix_discussion_turns_group_round",
            "discussion_group_id", "round_number", "turn_number",
        ),
    )

    id: Mapped[UUIDPk]
    discussion_group_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("discussion_groups.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    target_persona_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    turn_type: Mapped[str] = mapped_column(String(32), nullable=False)
    public_text: Mapped[str] = mapped_column(Text, nullable=False)
    stance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    referenced_turn_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    referenced_source_record_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    referenced_memory_atom_ids: Mapped[list["UUID"]] = mapped_column(  # type: ignore[type-arg]
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list,
    )
    psychology_control_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    forbidden_claim_audit: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    created_at: Mapped[CreatedAt]


class DiscussionPrivateBallot(Base):
    __tablename__ = "discussion_private_ballots"
    __table_args__ = (
        UniqueConstraint(
            "discussion_session_id", "persona_id", "ballot_stage",
            name="uq_discussion_private_ballots_unique",
        ),
        CheckConstraint(
            "ballot_stage IN ('pre','reflection','final')",
            name="ck_discussion_private_ballots_stage",
        ),
        CheckConstraint(
            "private_stance IN ('curious_but_unconvinced',"
            "'interested_if_proven','skeptical','likely_reject',"
            "'needs_more_information')",
            name="ck_discussion_private_ballots_stance",
        ),
        CheckConstraint(
            "confidence IN ('high','medium','low')",
            name="ck_discussion_private_ballots_confidence",
        ),
        CheckConstraint(
            "public_private_delta IS NULL OR public_private_delta IN ("
            "'private_acceptance','public_compliance_only','resistance',"
            "'no_change','polarization','uncertainty_increase')",
            name="ck_discussion_private_ballots_delta",
        ),
    )

    id: Mapped[UUIDPk]
    discussion_session_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("discussion_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    discussion_group_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("discussion_groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    persona_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_records.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ballot_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    private_stance: Mapped[str] = mapped_column(String(32), nullable=False)
    private_reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    public_private_delta: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    top_objection: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_proof_need: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[CreatedAt]


class PersonaMemoryAtom(Base):
    """Source-grounded persona memory atom.

    DB CHECK constraints prevent fake memories: every atom MUST carry a
    non-empty origin_excerpt + a UUID origin_ref_id + a closed-set
    origin_type. Atoms are immutable: if a memory is superseded, write a
    new atom with `invalidated_by_id` pointing back to it.
    """

    __tablename__ = "persona_memory_atoms"
    __table_args__ = (
        CheckConstraint(
            "memory_type IN ('evidence','trait','psychology',"
            "'prior_simulation','discussion_turn','private_ballot')",
            name="ck_persona_memory_atoms_memory_type",
        ),
        CheckConstraint(
            "origin_type IN ('source_record','persona_trait',"
            "'persona_psychology_trait','persona_evidence_link',"
            "'agent_response','discussion_turn','discussion_private_ballot')",
            name="ck_persona_memory_atoms_origin_type",
        ),
        CheckConstraint(
            "importance_score >= 1 AND importance_score <= 10",
            name="ck_persona_memory_atoms_importance_range",
        ),
        CheckConstraint(
            "char_length(origin_excerpt) >= 1",
            name="ck_persona_memory_atoms_origin_excerpt_nonempty",
        ),
        Index(
            "ix_persona_memory_atoms_persona_run",
            "persona_id", "run_scope_id",
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
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_ref_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True), nullable=False,
    )
    origin_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    memory_text: Mapped[str] = mapped_column(Text, nullable=False)
    importance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    recency_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    relevance_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list,
    )
    invalidated_by_id: Mapped["UUID | None"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("persona_memory_atoms.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[CreatedAt]
