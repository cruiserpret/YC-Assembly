"""Phase 15J — Validation Case Factory: CANDIDATE schema (NOT validation data).

A *candidate* is an externally-sourced market-outcome lead that a human has not
yet reviewed and approved. It is deliberately a DIFFERENT shape from a
``ValidationCase``:

  - it carries ``claimed_outcome_proportions`` (a CLAIM awaiting review), never a
    validated ``observed`` outcome,
  - ``extra="forbid"`` makes it impossible for a candidate JSON to smuggle in
    ledger-only fields (``observed`` / ``predicted`` / ``anti_overfit`` /
    ``metrics``) — those keys raise a ValidationError,
  - a ``purpose`` marker (mirroring ``acquisition_backlog.json``) documents that
    this object must NEVER be loaded as a validation case.

This module REUSES the canonical action-signal taxonomy and market-distribution
schema — it redefines neither. It defines only the candidate data shape; the
review gates, dedup, and promotion live in ``candidate_factory``. No forecast,
no calibration, no LLM, no network, no DB.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from assembly.market_calibration.action_signals import ActionSignal, SignalTier
from assembly.validation_ledger.schema import MarketDistribution, SourceType

# Isolation marker — a candidate is never validation data (cf. acquisition_backlog).
CANDIDATE_PURPOSE = "candidate_evaluation_not_validation_data"

CandidateStatus = Literal[
    "candidate",
    "needs_review",
    "rejected",
    "approved_for_pending",
    "approved_for_training",
    "approved_for_holdout",
]

# The three ledger destinations a candidate can be promoted into.
PromotionTarget = Literal["pending", "training", "holdout"]

# Reviewer answers are tri-state: an explicit yes/no is required to complete the
# checklist; "unknown" (the default) means the reviewer has not answered yet.
ChecklistAnswer = Literal["yes", "no", "unknown"]

SuitableFor = Literal["pending", "training", "holdout", "reject", "undecided"]

# The yes/no questions a reviewer MUST answer (not "unknown") for completion.
REQUIRED_CHECKLIST_ANSWERS = (
    "real_product_or_market_test",
    "outcome_externally_observable",
    "sources_provided",
    "population_or_source_biased",
    "enough_evidence_to_map_buckets",
    "should_reject",
)


class ReviewerChecklist(BaseModel):
    """The forced human review. A candidate may only be promoted once this is
    COMPLETE (every required question answered + a designation + an evidence
    tier when not rejecting)."""

    model_config = ConfigDict(extra="forbid")

    real_product_or_market_test: ChecklistAnswer = "unknown"
    outcome_externally_observable: ChecklistAnswer = "unknown"
    sources_provided: ChecklistAnswer = "unknown"
    population_or_source_biased: ChecklistAnswer = "unknown"
    bias_notes: str = ""
    enough_evidence_to_map_buckets: ChecklistAnswer = "unknown"
    evidence_tier: SignalTier | None = None
    uncertainty_flags: list[str] = Field(default_factory=list)
    suitable_for: SuitableFor = "undecided"
    should_reject: ChecklistAnswer = "unknown"
    reject_reason: str = ""
    reviewer: str = ""
    reviewed_at: str | None = None

    def unanswered(self) -> list[str]:
        """Required yes/no questions still left as 'unknown'."""
        return [q for q in REQUIRED_CHECKLIST_ANSWERS if getattr(self, q) == "unknown"]

    def is_complete(self) -> bool:
        if self.unanswered():
            return False
        if self.suitable_for == "undecided":
            return False
        # A non-rejecting decision must carry an explicit evidence tier.
        if self.suitable_for != "reject" and self.evidence_tier is None:
            return False
        return True


class CandidateCase(BaseModel):
    """An externally-sourced market-outcome candidate awaiting human review.

    NOT a validation case. ``extra="forbid"`` + the ``purpose`` marker guarantee
    it can never be loaded by the ledger, and that it cannot carry ledger-only
    outcome fields.
    """

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["candidate_evaluation_not_validation_data"] = CANDIDATE_PURPOSE
    candidate_id: str
    product_or_company_name: str
    category: str = "unknown"
    market_type: str = "unknown"
    launch_or_test_date: str = "unknown"  # ISO YYYY-MM-DD, or "unknown"
    source_urls: list[str] = Field(default_factory=list)
    source_type: SourceType = "unknown"
    candidate_summary: str = ""
    observed_outcome_summary: str = ""
    # A CLAIM about the four-bucket outcome — reviewer-mapped, awaiting approval.
    # Named distinctly from `observed` so it can never be mistaken for validated
    # outcome data. Reuses MarketDistribution => sum-to-100 ±1.5pp for free.
    claimed_outcome_proportions: MarketDistribution | None = None
    raw_outcome_evidence: str = ""
    # Reuses the canonical ActionSignal => tier auto-fills from SIGNAL_TIERS.
    action_signal_candidates: list[ActionSignal] = Field(default_factory=list)
    geographic_demographic_notes: str = ""
    reviewer_notes: str = ""
    reviewer_checklist: ReviewerChecklist | None = None
    evidence_tier: SignalTier | None = None
    uncertainty_flags: list[str] = Field(default_factory=list)
    status: CandidateStatus = "candidate"
    rejection_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @model_validator(mode="after")
    def _status_consistency(self) -> CandidateCase:
        if self.status == "rejected" and not (self.rejection_reason or "").strip():
            raise ValueError("a 'rejected' candidate must carry a rejection_reason")
        return self
