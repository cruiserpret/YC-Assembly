"""Phase 15B — validation-case ledger (data + metrics foundation).

This package holds the structured validation dataset that Assembly's future
calibration layer (Phase 15C+) will be measured against. It is data +
deterministic metrics only: NO calibration, NO learned ML, NO forecast
changes, NO LLM, NO network, NO production-simulation logic. Observed market
outcomes are used solely to SCORE predictions that were locked before the
outcome was known — never as a model input.

See docs/PHASE_15B_VALIDATION_LEDGER.md.
"""
from __future__ import annotations

from assembly.validation_ledger import metrics
from assembly.validation_ledger.loader import (
    DEFAULT_LEDGER_PATH,
    compute_case_metrics,
    holdout_cases,
    ledger_summary,
    load_cases,
    load_scored_ledger,
    scored_cases,
    training_cases,
    with_metrics,
)
from assembly.validation_ledger.schema import (
    AntiOverfit,
    CaseMetadata,
    FailureAnalysis,
    MarketDistribution,
    Metrics,
    ObservedProportions,
    PredictionLock,
    ValidationCase,
)

__all__ = [
    "metrics",
    "DEFAULT_LEDGER_PATH",
    "load_cases",
    "load_scored_ledger",
    "compute_case_metrics",
    "with_metrics",
    "training_cases",
    "holdout_cases",
    "scored_cases",
    "ledger_summary",
    "MarketDistribution",
    "ObservedProportions",
    "CaseMetadata",
    "PredictionLock",
    "Metrics",
    "FailureAnalysis",
    "AntiOverfit",
    "ValidationCase",
]
