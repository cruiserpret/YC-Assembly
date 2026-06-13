"""Phase 17B — AMFB-v1 canonical prediction schema (benchmark harness).

The single object EVERY benchmarked method emits for one case (Assembly, plain
LLMs, validation tools, surveys, human panels, naive baselines). A method that
cannot produce calibrated four-bucket proportions sets ``schema_failure=true``
(and records why). Pure data + validation: no LLM, no network, no DB, no forecast,
no calibration. This package is deliberately ISOLATED — it imports only stdlib +
pydantic and is NEVER imported by Assembly's forecast runtime or the validation
ledger loader. See docs/PHASE_17A_..._BENCHMARK_SPEC.md / docs/PHASE_17B_...md.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BENCHMARK_NAME = "assembly_market_fidelity_benchmark.v1"
BENCHMARK_SCHEMA_VERSION = "amfb.prediction.v1"

BUCKET_KEYS: tuple[str, ...] = (
    "buyer_action_positive",
    "receptive",
    "uncertain_proof_needed",
    "skeptical_resistant",
)
# Buckets are percentage points and must sum to ~100 unless schema_failure=true.
SUM_TOLERANCE_PP = 1.5

MethodClass = Literal[
    "assembly",
    "plain_llm",
    "validation_tool",
    "survey_platform",
    "human_panel",
    "naive_baseline",
]

LockMode = Literal[
    "manual_output", "dry_run", "naive", "future_provider_call", "live_provider_call",
]


class BenchmarkPrediction(BaseModel):
    """One method's prediction for one case. ``extra='forbid'`` — no unknown fields.

    If ``schema_failure`` is true the four buckets may be null (the method could not
    produce proportions) and ``schema_failure_reason`` MUST explain why; otherwise
    all four buckets are required, in [0, 100], and sum to ~100 (±SUM_TOLERANCE_PP).
    ``confidence`` is always required (explicit self-confidence in [0, 1]).
    """

    model_config = ConfigDict(extra="forbid")

    buyer_action_positive: float | None = None
    receptive: float | None = None
    uncertain_proof_needed: float | None = None
    skeptical_resistant: float | None = None
    confidence: float
    top_adoption_reasons: list[str] = Field(default_factory=list)
    top_rejection_reasons: list[str] = Field(default_factory=list)
    one_thing_needed: str = ""
    recommended_segment: str = ""
    expected_action_signal: str = ""
    forecast_notes: str = ""
    schema_failure: bool = False
    # Required when schema_failure=true; records WHY the method could not emit the schema.
    schema_failure_reason: str = ""

    @model_validator(mode="after")
    def _validate(self) -> BenchmarkPrediction:
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError("confidence must be in [0, 1]")
        if self.schema_failure:
            if not (self.schema_failure_reason or "").strip():
                raise ValueError(
                    "schema_failure=true requires a non-empty schema_failure_reason"
                )
            present = [k for k in BUCKET_KEYS if getattr(self, k) is not None]
            if present:
                raise ValueError(
                    "a schema_failure prediction must NOT carry buckets (it could not "
                    "produce proportions); remove: " + ", ".join(present)
                )
            return self
        missing = [k for k in BUCKET_KEYS if getattr(self, k) is None]
        if missing:
            raise ValueError(
                "a non-schema_failure prediction requires all four buckets; missing: "
                + ", ".join(missing)
            )
        for k in BUCKET_KEYS:
            v = float(getattr(self, k))
            if not (0.0 <= v <= 100.0):
                raise ValueError(f"bucket {k}={v} out of range [0, 100]")
        total = sum(float(getattr(self, k)) for k in BUCKET_KEYS)
        if abs(total - 100.0) > SUM_TOLERANCE_PP:
            raise ValueError(
                f"buckets sum to {total:.4f}, expected ~100 (±{SUM_TOLERANCE_PP})"
            )
        return self

    def buckets(self) -> dict[str, float]:
        """The four buckets as a plain dict (raises if schema_failure)."""
        if self.schema_failure:
            raise ValueError("a schema_failure prediction has no buckets")
        return {k: float(getattr(self, k)) for k in BUCKET_KEYS}

    def buckets_as_fractions(self) -> dict[str, float]:
        """Buckets renormalized to a probability distribution summing to 1.0 (for
        strictly-proper scoring). Raises if schema_failure."""
        b = self.buckets()
        total = sum(b.values()) or 1.0
        return {k: v / total for k, v in b.items()}

    def to_payload(self) -> dict:
        """The canonical, serializable prediction payload (drops Nones for a stable
        shape; schema_failure records keep only the fields they carry)."""
        return self.model_dump(mode="json", exclude_none=True)


def validate_prediction(payload: dict) -> BenchmarkPrediction:
    """Parse + validate a raw prediction dict. Raises pydantic ValidationError /
    ValueError on a malformed or non-summing prediction. Invents nothing."""
    return BenchmarkPrediction.model_validate(payload)
