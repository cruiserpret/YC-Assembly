"""Phase 17C — raw-vs-Assembly paired lift metrics (scaffold).

Assembly Lift = Assembly(base_model) score − Raw(base_model) score, on the SAME
frozen input bundle and the SAME base model. Pure functions; no real scores are
computed here (a later scoring phase supplies them). Lower benchmark scores are
better (e.g. Brier/MAE), so a POSITIVE lift means Assembly IMPROVED the base model.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, model_validator

from assembly.benchmarks.market_fidelity.run_metadata import RunMetadata


class PairingError(ValueError):
    """A raw/Assembly pair is invalid (different bundle or base model)."""


def verify_pairing(raw: RunMetadata, assembly: RunMetadata) -> list[str]:
    """Return a list of pairing issues (empty == a valid Raw/Assembly pair). The two
    runs MUST share the input bundle and base model, and be the right modes."""
    issues: list[str] = []
    if raw.mode != "raw_baseline":
        issues.append(f"raw run must be mode 'raw_baseline' (got {raw.mode!r})")
    if not assembly.is_assembly():
        issues.append("assembly run must be mode 'assembly_protocol' with assembly_protocol_enabled")
    if raw.input_bundle_hash != assembly.input_bundle_hash:
        issues.append(
            "raw and assembly runs MUST use the same input_bundle_hash "
            f"({raw.input_bundle_hash!r} != {assembly.input_bundle_hash!r})"
        )
    if (raw.base_model_family, raw.base_model_checkpoint) != (
        assembly.base_model_family, assembly.base_model_checkpoint
    ):
        issues.append(
            "raw and assembly runs MUST use the same base model "
            f"({raw.base_model_family}/{raw.base_model_checkpoint} != "
            f"{assembly.base_model_family}/{assembly.base_model_checkpoint})"
        )
    return issues


def assembly_lift(raw_score: float, assembly_score: float, *, lower_is_better: bool = True) -> float:
    """The lift Assembly's protocol added to the base model. With a lower-is-better
    score (Brier/MAE/etc.), lift = raw_score − assembly_score (positive == Assembly
    improved). With a higher-is-better score, lift = assembly_score − raw_score.
    Raises on non-finite scores (NaN/inf) so a bogus lift is never produced."""
    if not (math.isfinite(raw_score) and math.isfinite(assembly_score)):
        raise ValueError("scores must be finite numbers (got NaN/inf)")
    return (raw_score - assembly_score) if lower_is_better else (assembly_score - raw_score)


class PairedComparison(BaseModel):
    """A scored raw-vs-Assembly comparison for one base model on one case. ``lift`` is
    filled only by a later scoring phase; the harness verifies the pairing first."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    base_model_family: str
    base_model_checkpoint: str
    input_bundle_hash: str
    raw_baseline_id: str
    assembly_run_id: str
    same_input_bundle_verified: bool
    same_base_model_verified: bool
    metric_name: str | None = None
    raw_score: float | None = None
    assembly_score: float | None = None
    lift: float | None = None  # raw_score - assembly_score (lower-is-better); post-scoring only
    confidence_interval: list[float] | None = None  # [lo, hi]; later

    @model_validator(mode="after")
    def _scored_pair_is_consistent(self) -> PairedComparison:
        # A scored comparison must come from a VERIFIED pair, and a recorded lift must
        # match the scores — a mismatched/unverified pair cannot be silently scored.
        if self.raw_score is not None and self.assembly_score is not None:
            if not (self.same_input_bundle_verified and self.same_base_model_verified):
                raise ValueError(
                    "a scored PairedComparison requires same_input_bundle_verified and "
                    "same_base_model_verified to be True"
                )
            if self.lift is not None:
                expected = assembly_lift(self.raw_score, self.assembly_score)
                if abs(self.lift - expected) > 1e-9:
                    raise ValueError(
                        f"lift {self.lift} != raw_score - assembly_score ({expected})"
                    )
        return self
