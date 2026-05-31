"""Phase 15B — validation-ledger case schema (DESIGN/DATA layer only).

A *validation case* records, for one real product launch:
  - what Assembly predicted (locked BEFORE the outcome was known),
  - what the market actually did (observed proportions),
  - how the two compare (metrics, computed deterministically),
  - and the anti-overfit bookkeeping (training vs holdout).

This module defines ONLY the data shape + validation. It contains no model
logic, no calibration, no LLM, no network, no DB. It does not change any
forecast. It is the foundation the Phase 15 calibration layer will later be
validated against (see docs/PHASE_15B_VALIDATION_LEDGER.md).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from assembly.validation_ledger.metrics import BUCKET_KEYS

SourceType = Literal[
    "hacker_news",
    "product_hunt",
    "kickstarter",
    "reddit",
    "github",
    "app_store",
    "b2b",
    "mixed",
    "unknown",
]

ValidationStatus = Literal["scored", "partial", "pending", "excluded"]

DenominatorType = Literal[
    "comments",
    "independent_voices",
    "backers",
    "upvotes",
    "mixed_proxy",
    "unknown",
]

Confidence = Literal["low", "medium", "high"]

_SUM_TOLERANCE_PP = 1.5  # buckets are percentage points and must sum to ~100


class MarketDistribution(BaseModel):
    """A four-bucket market-proportion distribution in percentage points."""

    model_config = ConfigDict(extra="forbid")

    buyer_action_positive: float
    receptive: float
    uncertain_proof_needed: float
    skeptical_resistant: float
    # Optional 5th channel for off-topic / non-market noise, if a source needs
    # to account for it; included in the sum check when present.
    noise_meta: float | None = None

    @field_validator(
        "buyer_action_positive",
        "receptive",
        "uncertain_proof_needed",
        "skeptical_resistant",
        "noise_meta",
    )
    @classmethod
    def _in_range(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not (0.0 <= float(v) <= 100.0):
            raise ValueError(f"bucket value {v} out of range [0, 100]")
        return v

    @model_validator(mode="after")
    def _sums_to_100(self) -> MarketDistribution:
        total = (
            self.buyer_action_positive
            + self.receptive
            + self.uncertain_proof_needed
            + self.skeptical_resistant
            + (self.noise_meta or 0.0)
        )
        if abs(total - 100.0) > _SUM_TOLERANCE_PP:
            raise ValueError(
                f"distribution sums to {total:.3f}, expected ~100 "
                f"(tolerance ±{_SUM_TOLERANCE_PP})"
            )
        return self

    def to_buckets(self) -> dict[str, float]:
        """Return the four canonical buckets as a plain dict for metrics."""
        return {k: float(getattr(self, k)) for k in BUCKET_KEYS}


class ObservedProportions(MarketDistribution):
    """Observed market proportions + how they were measured."""

    denominator_type: DenominatorType = "unknown"
    denominator_count: int | None = None
    observation_confidence: Confidence = "medium"
    observation_notes: str = ""


class CaseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_name: str
    anonymized_name: str | None = None
    source_type: SourceType
    product_category: str
    launch_stage: str
    date_run: str  # ISO date string (YYYY-MM-DD); kept as str to stay JSON-pure
    validation_status: ValidationStatus
    confidence: Confidence = "medium"
    notes: str = ""


class PredictionLock(BaseModel):
    """References that make the prediction auditable + leakage-checkable."""

    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    simulation_id: str | None = None
    brief_hash: str | None = None
    evidence_snapshot_id: str | None = None
    evidence_snapshot_hash: str | None = None
    prediction_hash: str | None = None
    locked_prediction_created_at: str | None = None  # ISO date/datetime string
    leakage_risk: Confidence | Literal["unknown"] = "unknown"
    clean_room_notes: str = ""


class Metrics(BaseModel):
    """Computed comparison metrics. Optional in storage — computed on load via
    `loader.compute_case_metrics`. Never used as a model input."""

    model_config = ConfigDict(extra="forbid")

    mae_pp: float | None = None
    tvd: float | None = None
    max_bucket_error_pp: float | None = None
    direction_match: bool | None = None
    buyer_false_confidence: bool | None = None
    objection_overlap_score: float | None = None
    qualitative_verdict: str = ""


class FailureAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missed_bucket: str | None = None
    overpredicted_bucket: str | None = None
    underpredicted_bucket: str | None = None
    root_cause_tags: list[str] = Field(default_factory=list)
    recommended_followup: str = ""
    source_bias_notes: str = ""
    category_prior_notes: str = ""


class AntiOverfit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    used_for_training: bool = False
    used_for_holdout: bool = False
    excluded_from_training_reason: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _not_both_train_and_holdout(self) -> AntiOverfit:
        # A case must never be both a training and a holdout case — that is the
        # exact leakage the ledger exists to prevent.
        if self.used_for_training and self.used_for_holdout:
            raise ValueError(
                "a case cannot be both used_for_training and used_for_holdout"
            )
        return self


class ValidationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    metadata: CaseMetadata
    prediction_lock: PredictionLock = Field(default_factory=PredictionLock)
    predicted: MarketDistribution | None = None
    observed: ObservedProportions | None = None
    metrics: Metrics | None = None
    failure_analysis: FailureAnalysis | None = None
    anti_overfit: AntiOverfit = Field(default_factory=AntiOverfit)

    @model_validator(mode="after")
    def _scored_requires_pred_and_obs(self) -> ValidationCase:
        if self.metadata.validation_status == "scored" and (
            self.predicted is None or self.observed is None
        ):
            raise ValueError(
                f"case {self.case_id!r} is 'scored' but is missing "
                "predicted and/or observed proportions"
            )
        return self

    def is_scorable(self) -> bool:
        """True iff metrics can be computed (predicted + observed both present)."""
        return self.predicted is not None and self.observed is not None
