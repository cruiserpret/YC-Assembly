"""Phase 17D — historical-case OUTCOME RECORD (kept SEPARATE from the input bundle).

The outcome record holds the actual realized result. It is NEVER shown to the model —
it is used only AFTER the prediction is locked, for scoring. A buyer/action numerator
alone must NOT fabricate the non-buyer buckets: only a directly-defensible measurement
may carry a full four-bucket distribution. Pure data + validation.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

OutcomeType = Literal[
    "action_anchor_only",
    "direct_observed_distribution",
    "conversion",
    "revenue",
    "signup_waitlist",
    "producthunt_rank",
    "survey_distribution",
    "other",
]
ScoringMappingType = Literal[
    "action_anchor_only", "direct_observed_distribution", "partial_conversion",
    "evidence_only", "not_scoreable",
]
_BUCKETS = ("buyer_action_positive", "receptive", "uncertain_proof_needed", "skeptical_resistant")


class OutcomeRecord(BaseModel):
    """The realized outcome, used ONLY post-lock for scoring (never a model input)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    outcome_timestamp: str
    outcome_type: OutcomeType
    observed_result: str = ""  # human-readable summary of what actually happened
    scoring_mapping_type: ScoringMappingType
    buyer_action_positive_observed: float | None = None
    full_distribution_observed: dict | None = None  # 4 buckets, ONLY if directly defensible
    metrics: dict = Field(default_factory=dict)  # backers/customers/users/revenue/rank/etc.
    outcome_sources: list[str] = Field(default_factory=list)
    outcome_source_hashes: list[str] = Field(default_factory=list)
    mapping_limitations: str = ""
    # Scoreability (mutually-informative flags; at least one must be set true OR not_scoreable).
    full_distribution_scoreable: bool = False
    buyer_anchor_scoreable: bool = False
    qualitative_scoreable: bool = False
    not_scoreable: bool = False

    @model_validator(mode="after")
    def _validate(self) -> OutcomeRecord:
        # A full four-bucket distribution may exist ONLY for a directly-defensible
        # measurement — never fabricated from a buyer/action anchor alone.
        if self.full_distribution_observed is not None:
            if self.scoring_mapping_type != "direct_observed_distribution":
                raise ValueError(
                    "full_distribution_observed requires scoring_mapping_type="
                    "'direct_observed_distribution' (a buyer anchor must not fabricate buckets)"
                )
            missing = [b for b in _BUCKETS if b not in self.full_distribution_observed]
            if missing:
                raise ValueError(f"full_distribution_observed missing buckets: {missing}")
            extra = set(self.full_distribution_observed) - set(_BUCKETS)
            if extra:
                raise ValueError(f"full_distribution_observed has unknown buckets: {sorted(extra)}")
            for b in _BUCKETS:
                v = float(self.full_distribution_observed[b])
                if not (0.0 <= v <= 100.0):
                    raise ValueError(f"full_distribution_observed[{b}]={v} out of range [0, 100]")
            total = sum(float(self.full_distribution_observed[b]) for b in _BUCKETS)
            if abs(total - 100.0) > 1.5:
                raise ValueError(f"full_distribution_observed sums to {total:.3f}, expected ~100")
            if not self.full_distribution_scoreable:
                raise ValueError("a full_distribution_observed must set full_distribution_scoreable=true")
        # action_anchor_only must NOT carry a full distribution.
        if self.scoring_mapping_type == "action_anchor_only" and self.full_distribution_observed is not None:
            raise ValueError("action_anchor_only must not carry a full four-bucket distribution")
        if not self.scoring_mapping_type == "direct_observed_distribution" and self.full_distribution_scoreable:
            raise ValueError("full_distribution_scoreable=true requires a direct_observed_distribution mapping")
        # 'not_scoreable' must be internally consistent: the mapping type and the flag
        # agree, and a not_scoreable outcome carries no positive scoreability flags.
        if self.scoring_mapping_type == "not_scoreable" and not self.not_scoreable:
            raise ValueError("scoring_mapping_type='not_scoreable' requires not_scoreable=true")
        if self.not_scoreable and any(
            [self.full_distribution_scoreable, self.buyer_anchor_scoreable, self.qualitative_scoreable]
        ):
            raise ValueError("not_scoreable=true cannot be combined with a positive scoreability flag")
        if not any(
            [self.full_distribution_scoreable, self.buyer_anchor_scoreable,
             self.qualitative_scoreable, self.not_scoreable]
        ):
            raise ValueError(
                "set at least one scoreability flag (full_distribution / buyer_anchor / qualitative / not_scoreable)"
            )
        return self
