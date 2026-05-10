"""Phase 10A — ORM models for the API run-tracking layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assembly.db import Base
from assembly.models._types import CreatedAt, UUIDPk


RUN_MODES: tuple[str, ...] = ("fixture_demo", "live_founder_brief")
RUN_STATUSES: tuple[str, ...] = (
    "pending", "running", "complete", "failed", "skeletal",
)
RUN_STAGES: tuple[str, ...] = (
    "validating_brief",
    "planning_evidence",
    "retrieving_evidence",
    "scoring_evidence",
    "building_personas",
    "enriching_psychology",
    "running_individual_simulation",
    "running_group_discussion",
    "repairing_incomplete_outputs",
    "building_cohorts",
    "inferring_simulated_intent",
    "running_society_wide_debate",
    "generating_report",
    "complete",
    "failed",
)
ARTIFACT_TYPES: tuple[str, ...] = (
    "report_json", "report_markdown",
    "personas_json", "cohorts_json", "discussion_json", "intent_json",
    "audit_json", "discussion_quality_json", "cohort_quality_json",
    "intent_quality_json",
)


class AssemblyRun(Base):
    __tablename__ = "assembly_runs"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('fixture_demo','live_founder_brief')",
            name="ck_assembly_runs_mode",
        ),
        CheckConstraint(
            "status IN ('pending','running','complete','failed','skeletal')",
            name="ck_assembly_runs_status",
        ),
        CheckConstraint(
            "current_stage IN ('validating_brief','planning_evidence',"
            "'retrieving_evidence','scoring_evidence','building_personas',"
            "'enriching_psychology','running_individual_simulation',"
            "'running_group_discussion','repairing_incomplete_outputs',"
            "'building_cohorts','inferring_simulated_intent',"
            "'running_society_wide_debate','generating_report',"
            "'complete','failed')",
            name="ck_assembly_runs_current_stage",
        ),
        Index("ix_assembly_runs_status", "status"),
    )

    id: Mapped[UUIDPk]
    user_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    product_brief: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_stage: Mapped[str] = mapped_column(
        String(48), nullable=False,
    )
    stage_progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    artifact_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_run_scope_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


class AssemblyRunArtifact(Base):
    __tablename__ = "assembly_run_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "artifact_type",
            name="uq_assembly_run_artifacts_run_type",
        ),
        CheckConstraint(
            "artifact_type IN ('report_json','report_markdown',"
            "'personas_json','cohorts_json','discussion_json',"
            "'intent_json','audit_json','discussion_quality_json',"
            "'cohort_quality_json','intent_quality_json')",
            name="ck_assembly_run_artifacts_type",
        ),
        CheckConstraint(
            "char_length(path) >= 1",
            name="ck_assembly_run_artifacts_path_nonempty",
        ),
    )

    id: Mapped[UUIDPk]
    run_id: Mapped["UUID"] = mapped_column(  # type: ignore[type-arg]
        UUID(as_uuid=True),
        ForeignKey("assembly_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="application/json",
    )
    is_user_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )
    created_at: Mapped[CreatedAt]
