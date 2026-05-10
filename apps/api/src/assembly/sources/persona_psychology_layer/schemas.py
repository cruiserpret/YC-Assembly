"""Phase 9A.3 — schemas for the persona psychology layer.

`extra="forbid"` discipline. Closed-set Literals. Every trait carries
either evidence_basis (with at least one of the source-id/trait-id/
response-id arrays non-empty) or a neutral_default with a caveat.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PsychologyTraitName = Literal[
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
]
ValueLabel = Literal["low", "medium", "high"]
Confidence = Literal["high", "medium", "low"]
InferenceMethod = Literal[
    "evidence_direct",
    "simulation_behavior",
    "role_context_prior",
    "neutral_default",
]


OCEAN_TRAITS: tuple[str, ...] = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
)
ADDITIONAL_REQUIRED_TRAITS: tuple[str, ...] = (
    "risk_tolerance",
    "novelty_seeking",
    "trust_proof_threshold",
    "social_influence_susceptibility",
    "category_involvement_or_expertise",
)
PRICE_SENSITIVITY_TRAIT: str = "price_sensitivity"
ALL_REQUIRED_OCEAN_PLUS_FIVE: tuple[str, ...] = (
    *OCEAN_TRAITS,
    *ADDITIONAL_REQUIRED_TRAITS,
)


class PsychologyTrait(BaseModel):
    """One inferred psychology trait for one persona, one run scope.

    Either `evidence_basis` is non-empty AND at least one source-id /
    trait-id / response-id array is non-empty (when inference_method !=
    'neutral_default'), or inference_method == 'neutral_default' AND
    `caveat` is non-empty. The Pydantic-side check mirrors the DB CHECK.
    """

    model_config = ConfigDict(extra="forbid")

    trait_name: PsychologyTraitName
    value_numeric: float = Field(ge=0.0, le=1.0)
    value_label: ValueLabel
    confidence: Confidence
    inference_method: InferenceMethod
    evidence_basis: str | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    source_trait_ids: list[str] = Field(default_factory=list)
    simulation_response_ids: list[str] = Field(default_factory=list)
    caveat: str | None = None

    @model_validator(mode="after")
    def _basis_or_caveat(self) -> PsychologyTrait:
        if self.inference_method == "neutral_default":
            if not (self.caveat or "").strip():
                raise ValueError(
                    f"trait {self.trait_name}: neutral_default requires "
                    "a non-empty caveat"
                )
        else:
            if not (self.evidence_basis or "").strip():
                raise ValueError(
                    f"trait {self.trait_name}: inference_method "
                    f"{self.inference_method} requires non-empty "
                    "evidence_basis"
                )
        if self.value_label == "low" and self.value_numeric > 0.4:
            raise ValueError(
                f"trait {self.trait_name}: label/value mismatch "
                f"(label=low but value={self.value_numeric})"
            )
        if self.value_label == "high" and self.value_numeric < 0.6:
            raise ValueError(
                f"trait {self.trait_name}: label/value mismatch "
                f"(label=high but value={self.value_numeric})"
            )
        return self


class PsychologyProfile(BaseModel):
    """Full psychology profile for one persona in one run scope."""

    model_config = ConfigDict(extra="forbid")

    persona_id: str
    run_scope_id: str
    target_brief: str
    generated_for_phase: str = "9A.3"
    traits: list[PsychologyTrait] = Field(min_length=10, max_length=11)

    @model_validator(mode="after")
    def _required_traits(self) -> PsychologyProfile:
        names = [t.trait_name for t in self.traits]
        seen = set(names)
        missing_ocean = [n for n in OCEAN_TRAITS if n not in seen]
        if missing_ocean:
            raise ValueError(
                f"profile for persona {self.persona_id} missing OCEAN "
                f"trait(s): {missing_ocean}"
            )
        missing_add = [
            n for n in ADDITIONAL_REQUIRED_TRAITS if n not in seen
        ]
        if missing_add:
            raise ValueError(
                f"profile for persona {self.persona_id} missing required "
                f"additional trait(s): {missing_add}"
            )
        if len(seen) != len(names):
            raise ValueError(
                f"profile for persona {self.persona_id} has duplicate "
                "trait_name entries"
            )
        return self
