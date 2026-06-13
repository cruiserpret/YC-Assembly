"""Phase 17C — model-agnostic run metadata + raw-vs-Assembly lanes.

Assembly is a market-prediction ARCHITECTURE, not an Anthropic/GPT wrapper. The
benchmark must be able to describe a prediction run over ANY base model (open-weight
or hosted) and pair ``Raw(base_model)`` against ``Assembly(base_model)`` on the SAME
frozen input bundle. This module is pure data + validation; it imports only stdlib +
pydantic and never touches the forecast runtime, calibration, config, or the
validation ledger. No model is loaded or called here.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Assembly runs in one of two MODES against a base model:
RunMode = Literal["raw_baseline", "assembly_protocol"]

# Deliberately open-ended; Anthropic is NOT the default brain.
BaseModelFamily = Literal[
    "qwen", "llama", "mistral", "deepseek", "gemma", "phi",
    "claude", "gpt", "gemini", "other",
]
ModelProvider = Literal["local", "hosted", "openai", "anthropic", "google", "other"]
ContaminationRisk = Literal["none", "low", "medium", "high", "unknown"]
# Blindness tiers are defined in blindness.py; mirrored here as the metadata value.
BlindnessTierId = Literal[0, 1, 2, 3, 4]

# The five benchmark LANES a record can belong to.
LaneType = Literal[
    "raw_base_model",
    "assembly_with_same_base_model",
    "naive_baseline",
    "human_or_survey_baseline",
    "competitor_tool_baseline",
]


class RunMetadata(BaseModel):
    """Full description of ONE prediction run (raw or Assembly) over a base model."""

    model_config = ConfigDict(extra="forbid")

    mode: RunMode
    base_model_family: BaseModelFamily
    base_model_checkpoint: str
    model_provider: ModelProvider
    model_release_date: str | None = None  # ISO date if known
    training_cutoff: str | None = None  # ISO date if known
    local_or_remote: Literal["local", "remote"]
    web_enabled: bool = False
    rag_enabled: bool = False
    tools_enabled: bool = False
    assembly_protocol_enabled: bool = False
    temperature: float | None = None
    seed: int | None = None
    input_bundle_hash: str
    output_prediction_hash: str | None = None
    contamination_risk: ContaminationRisk = "unknown"
    blindness_tier: BlindnessTierId | None = None

    def is_assembly(self) -> bool:
        return self.mode == "assembly_protocol" and self.assembly_protocol_enabled


class BenchmarkLane(BaseModel):
    """Lane metadata linking a record to its lane and (for Assembly runs) to the
    paired Raw run on the SAME input bundle. ``assembly_lift_metrics`` is a
    placeholder filled only by a later scoring phase (never at lock)."""

    model_config = ConfigDict(extra="forbid")

    lane_type: LaneType
    base_model_family: BaseModelFamily | None = None
    paired_raw_baseline_id: str | None = None
    paired_assembly_run_id: str | None = None
    same_input_bundle_required: Literal[True] = True
    assembly_lift_metrics: dict | None = None  # placeholder; populated post-scoring only
