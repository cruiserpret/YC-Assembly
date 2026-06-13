"""Assembly Market Fidelity Benchmark v1 (AMFB-v1) harness — Phase 17B.

Executable infrastructure to LATER lock baseline predictions (plain LLMs, manually
pasted LLM outputs, naive/statistical baselines) under fair-comparison discipline:
same input bundle, same schema, canonical JSON, hash-locked, immutable records, no
post-outcome edits, no leakage. Phase 17B makes NO paid provider calls (provider
adapters are disabled stubs) and runs NO benchmark; it only builds the harness.

ISOLATION: this package imports only stdlib + pydantic. It does NOT import
Assembly's forecast runtime, calibration, config, or the validation ledger, and is
never imported by them. Baseline records live under
``apps/api/benchmarks/market_fidelity/baseline_predictions/`` and are never loaded as
validation cases.
"""
from __future__ import annotations

from assembly.benchmarks.market_fidelity.baseline_records import (
    BaselinePredictionRecord,
    default_records_dir,
    load_records,
    write_record,
)
from assembly.benchmarks.market_fidelity.canonicalize import canonical_bytes, canonical_json
from assembly.benchmarks.market_fidelity.hash_lock import (
    compute_prediction_hash,
    input_bundle_hash,
)
from assembly.benchmarks.market_fidelity.naive_baselines import NAIVE_BASELINE_IDS, naive_baseline
from assembly.benchmarks.market_fidelity.schema import (
    BENCHMARK_NAME,
    BENCHMARK_SCHEMA_VERSION,
    BUCKET_KEYS,
    BenchmarkPrediction,
    validate_prediction,
)

__all__ = [
    "BENCHMARK_NAME",
    "BENCHMARK_SCHEMA_VERSION",
    "BUCKET_KEYS",
    "BenchmarkPrediction",
    "validate_prediction",
    "canonical_json",
    "canonical_bytes",
    "input_bundle_hash",
    "compute_prediction_hash",
    "BaselinePredictionRecord",
    "default_records_dir",
    "write_record",
    "load_records",
    "NAIVE_BASELINE_IDS",
    "naive_baseline",
]
