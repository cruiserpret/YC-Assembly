"""Phase 8.2A — PopulationConstructionAudit Pydantic builder + validator.

The audit row is the audience-strength fingerprint that the future
Population-Mode UI surfaces at the top of every report. Phase 8.2A ships
the builder + validator only; no Population-Mode runs exist yet to
populate it. Tests use seeded/fake data.

Design intent:
  - Every Population-Mode run produces exactly one
    `population_construction_audit` row.
  - The audit MUST contain society size + source breakdown + geography
    label + society-strength label.
  - The UI never hides the audit panel.
  - The strength label is honest: "thin / moderate / strong" — never a
    percentage and never a forecast.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from assembly.pipeline.persona.constants import COVERAGE_LABELS
from assembly.pipeline.persona.validator import (
    ValidationResult,
    validate_population_audit_payload,
)


CoverageLabel = str  # constrained via field_validator below


class RequestedSocietySpec(BaseModel):
    """Compact echo of the brief slice that drove audience retrieval."""

    model_config = ConfigDict(extra="forbid")

    target_market: str | None = None
    geography_broad: str | None = None
    segments_implied: list[str] = Field(default_factory=list)


class TraitSupportRow(BaseModel):
    """Per-field direct/inferred/unknown breakdown."""

    model_config = ConfigDict(extra="forbid")

    direct_pct: float = Field(ge=0.0, le=1.0)
    inferred_pct: float = Field(ge=0.0, le=1.0)
    unknown_pct: float = Field(ge=0.0, le=1.0)


class PopulationConstructionAuditPayload(BaseModel):
    """Pydantic shape for the `population_construction_audit` row.

    The DB row's columns mirror these exactly. Use
    `build_population_construction_audit_payload(...)` to construct it
    safely.
    """

    model_config = ConfigDict(extra="forbid")

    requested_society: RequestedSocietySpec
    retrieved_persona_count: int = Field(ge=0)
    final_persona_count: int = Field(ge=0)
    cluster_count: int = Field(ge=0)
    source_kind_counts: dict[str, int] = Field(default_factory=dict)
    direct_trait_count: int = Field(ge=0, default=0)
    inferred_trait_count: int = Field(ge=0, default=0)
    unknown_trait_count: int = Field(ge=0, default=0)
    missing_trait_count: int = Field(ge=0, default=0)
    trait_support_breakdown: dict[str, TraitSupportRow] = Field(default_factory=dict)
    geography_coverage_label: CoverageLabel
    geography_coverage_notes: str | None = None
    source_freshness_label: CoverageLabel | None = None
    representativeness_caveats: list[str] = Field(default_factory=list)
    missing_evidence_warnings: list[str] = Field(default_factory=list)
    compliance_status: dict[str, Any] = Field(default_factory=dict)
    society_strength_label: CoverageLabel
    society_strength_explanation: str | None = None

    @field_validator("geography_coverage_label", "society_strength_label")
    @classmethod
    def _required_coverage_label(cls, v: str) -> str:
        if v not in COVERAGE_LABELS:
            raise ValueError(
                f"label must be one of {list(COVERAGE_LABELS)}; got {v!r}"
            )
        return v

    @field_validator("source_freshness_label")
    @classmethod
    def _optional_coverage_label(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in COVERAGE_LABELS:
            raise ValueError(
                f"label must be one of {list(COVERAGE_LABELS)} or None; got {v!r}"
            )
        return v


def build_population_construction_audit_payload(**fields: Any) -> PopulationConstructionAuditPayload:
    """Construct + Pydantic-validate the audit payload from raw fields.

    Use this in future Population-Mode code to ensure shape correctness
    before persisting. Test fixtures use this directly to exercise the
    schema."""
    return PopulationConstructionAuditPayload.model_validate(fields)


def validate_population_construction_audit_payload(
    payload: dict[str, Any],
) -> ValidationResult:
    """Re-export of `validator.validate_population_audit_payload` under
    the audit-module name so callers can import from one place."""
    return validate_population_audit_payload(payload)


def render_audit_summary(audit: PopulationConstructionAuditPayload) -> str:
    """Human-readable one-liner for the audit panel header. Used by the
    future UI; tested in 8.2A."""
    geo = audit.geography_coverage_label
    society = audit.society_strength_label
    fresh = audit.source_freshness_label or "n/a"
    return (
        f"{audit.final_persona_count:,} anonymous source-grounded persona "
        f"node(s). Society strength: {society}. Geography: {geo}. "
        f"Source freshness: {fresh}."
    )
