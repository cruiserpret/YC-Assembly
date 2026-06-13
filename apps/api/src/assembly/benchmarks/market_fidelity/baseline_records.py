"""Phase 17B — immutable benchmark baseline-prediction records (audit-only).

A locked prediction from one method for one case. These records live UNDER
``apps/api/benchmarks/market_fidelity/baseline_predictions/`` — NOT under
``validation_cases/`` — so they are NEVER loaded by the validation ledger and never
become validation/training/holdout data. They carry a purpose marker, are
observed-free at lock time (the outcome is added only by a later scoring phase),
and are written only on an explicit opt-in. Pure data + filesystem; no network,
no provider calls, no forecast/calibration.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from assembly.benchmarks.market_fidelity.schema import (
    BENCHMARK_NAME,
    LockMode,
    MethodClass,
    validate_prediction,
)

RECORD_PURPOSE = "benchmark_baseline_prediction_not_validation_data"


class BaselinePredictionRecord(BaseModel):
    """One immutable, hash-locked benchmark prediction. ``extra='forbid'``."""

    model_config = ConfigDict(extra="forbid")

    benchmark: Literal["assembly_market_fidelity_benchmark.v1"] = BENCHMARK_NAME
    purpose: Literal["benchmark_baseline_prediction_not_validation_data"] = RECORD_PURPOSE
    benchmark_case_id: str
    method_class: MethodClass
    method_id: str
    method_version: str
    provider: str = ""
    input_bundle_hash: str
    prediction_payload: dict
    prediction_hash: str
    locked_at: str
    cost_usd: float = 0.0
    runtime_seconds: float = 0.0
    mode: LockMode
    leakage_status: str = "clean_pre_outcome"
    schema_failure: bool = False
    notes: str = ""
    # The outcome is NEVER written at lock time — only by a later scoring phase.
    observed: None = None

    @model_validator(mode="after")
    def _payload_conforms_to_schema(self) -> BaselinePredictionRecord:
        # Force EVERY writer (not just the CLI) through the AMFB-v1 schema, so a record
        # constructed directly cannot embed a fabricated outcome or off-schema field
        # in prediction_payload (BenchmarkPrediction is extra='forbid').
        validate_prediction(self.prediction_payload)
        return self


def default_records_dir() -> Path:
    """``apps/api/benchmarks/market_fidelity/baseline_predictions/`` — resolved from
    this module's location (apps/api/src/assembly/benchmarks/market_fidelity/ → up 4
    to apps/api/). Audit-only; absent from validation_cases/manifest.json."""
    api_root = Path(__file__).resolve().parents[4]
    return api_root / "benchmarks" / "market_fidelity" / "baseline_predictions"


def record_filename(record: BaselinePredictionRecord) -> str:
    safe_case = "".join(c if c.isalnum() or c in "-_" else "_" for c in record.benchmark_case_id)
    safe_method = "".join(c if c.isalnum() or c in "-_" else "_" for c in record.method_id)
    digest = record.prediction_hash.split(":", 1)[-1][:12]
    return f"{safe_case}__{safe_method}__{digest}.json"


def write_record(
    record: BaselinePredictionRecord,
    *,
    allow_write: bool = False,
    records_dir: str | Path | None = None,
) -> Path | None:
    """Persist a record ONLY when ``allow_write=True`` (dry-run by default writes
    nothing and returns None). Writes exclusively under the benchmark records dir;
    refuses to overwrite an existing file (records are immutable)."""
    if not allow_write:
        return None
    base = Path(records_dir) if records_dir is not None else default_records_dir()
    base.mkdir(parents=True, exist_ok=True)
    out = base / record_filename(record)
    if out.exists():
        raise ValueError(f"record already exists (immutable): {out.name}")
    out.write_text(json.dumps(record.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    return out


def load_records(records_dir: str | Path | None = None) -> list[BaselinePredictionRecord]:
    """Load all baseline records (audit/scoring use only — never the validation
    ledger)."""
    base = Path(records_dir) if records_dir is not None else default_records_dir()
    if not base.exists():
        return []
    out: list[BaselinePredictionRecord] = []
    for p in sorted(base.glob("*.json")):
        out.append(BaselinePredictionRecord.model_validate(json.loads(p.read_text(encoding="utf-8"))))
    return out
