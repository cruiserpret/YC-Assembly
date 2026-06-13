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

# Phase 17C — open-weight blind backtest architecture (model-agnostic, offline, blind).
from assembly.benchmarks.market_fidelity.audit_records import (
    BacktestAuditRecord,
    default_audits_dir,
    load_audit_records,
    write_audit_record,
)
from assembly.benchmarks.market_fidelity.baseline_records import (
    BaselinePredictionRecord,
    default_records_dir,
    load_records,
    write_record,
)
from assembly.benchmarks.market_fidelity.blindness import (
    PUBLIC_CLAIM_TIERS,
    TIER_DEFINITIONS,
    is_public_claim_grade,
)
from assembly.benchmarks.market_fidelity.canonicalize import canonical_bytes, canonical_json
from assembly.benchmarks.market_fidelity.eligibility import (
    EligibilityResult,
    RetrospectiveCaseEligibilityInput,
    evaluate_eligibility,
)
from assembly.benchmarks.market_fidelity.hash_lock import (
    compute_prediction_hash,
    input_bundle_hash,
)
from assembly.benchmarks.market_fidelity.knowledge_probe import (
    KnowledgeProbeResult,
    assess_probe,
    build_probe_questions,
    probe_blocks_public_claim,
)
from assembly.benchmarks.market_fidelity.lift import (
    PairedComparison,
    assembly_lift,
    verify_pairing,
)
from assembly.benchmarks.market_fidelity.naive_baselines import NAIVE_BASELINE_IDS, naive_baseline
from assembly.benchmarks.market_fidelity.offline_policy import (
    is_offline_blind_ok,
    validate_offline_blind_run_config,
)
from assembly.benchmarks.market_fidelity.retrieval_filter import filter_pre_outcome_evidence
from assembly.benchmarks.market_fidelity.run_metadata import BenchmarkLane, RunMetadata
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
    # Phase 17C
    "RunMetadata",
    "BenchmarkLane",
    "verify_pairing",
    "assembly_lift",
    "PairedComparison",
    "validate_offline_blind_run_config",
    "is_offline_blind_ok",
    "filter_pre_outcome_evidence",
    "build_probe_questions",
    "assess_probe",
    "KnowledgeProbeResult",
    "probe_blocks_public_claim",
    "TIER_DEFINITIONS",
    "PUBLIC_CLAIM_TIERS",
    "is_public_claim_grade",
    "RetrospectiveCaseEligibilityInput",
    "EligibilityResult",
    "evaluate_eligibility",
    "BacktestAuditRecord",
    "default_audits_dir",
    "write_audit_record",
    "load_audit_records",
]
