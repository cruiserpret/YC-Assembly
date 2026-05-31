"""Phase 15B — load + score the validation ledger.

Reads the JSON ledger, parses it into validated `ValidationCase` objects,
computes metrics deterministically for scorable cases, and exposes
training/holdout filters. No LLM, no network, no DB. Observed data is used
ONLY to score locked predictions — never as a model input.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from assembly.validation_ledger import metrics
from assembly.validation_ledger.schema import Metrics, ValidationCase

# apps/api/validation_cases/seed_cases.json
#   this file: apps/api/src/assembly/validation_ledger/loader.py
#   parents[3] -> apps/api
DEFAULT_LEDGER_PATH = (
    Path(__file__).resolve().parents[3] / "validation_cases" / "seed_cases.json"
)


def load_cases(path: str | Path | None = None) -> list[ValidationCase]:
    """Load + validate every case in the ledger JSON.

    The JSON is a list of case objects (or an object with a ``cases`` list).
    Raises pydantic ValidationError on any malformed case, and ValueError on
    duplicate case_ids.
    """
    p = Path(path) if path is not None else DEFAULT_LEDGER_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    items = raw["cases"] if isinstance(raw, dict) else raw
    cases = [ValidationCase.model_validate(item) for item in items]
    assert_unique_case_ids(cases)
    return cases


def assert_unique_case_ids(cases: Iterable[ValidationCase]) -> None:
    seen: set[str] = set()
    for c in cases:
        if c.case_id in seen:
            raise ValueError(f"duplicate case_id in ledger: {c.case_id!r}")
        seen.add(c.case_id)


def compute_case_metrics(case: ValidationCase) -> Metrics | None:
    """Compute metrics for a scorable case, else None (graceful for partial /
    pending cases with missing predicted or observed data)."""
    if not case.is_scorable():
        return None
    assert case.predicted is not None and case.observed is not None  # narrow
    result = metrics.compute_all(
        case.predicted.to_buckets(), case.observed.to_buckets()
    )
    return Metrics(
        mae_pp=result["mae_pp"],
        tvd=result["tvd"],
        max_bucket_error_pp=result["max_bucket_error_pp"],
        direction_match=result["direction_match"],
        buyer_false_confidence=result["buyer_false_confidence"],
        objection_overlap_score=(
            case.metrics.objection_overlap_score if case.metrics else None
        ),
        qualitative_verdict=(case.metrics.qualitative_verdict if case.metrics else ""),
    )


def with_metrics(case: ValidationCase) -> ValidationCase:
    """Return a copy of the case with freshly-computed metrics attached
    (no-op for non-scorable cases)."""
    m = compute_case_metrics(case)
    if m is None:
        return case
    return case.model_copy(update={"metrics": m})


def load_scored_ledger(path: str | Path | None = None) -> list[ValidationCase]:
    """Load the ledger with metrics computed for every scorable case."""
    return [with_metrics(c) for c in load_cases(path)]


def training_cases(cases: Iterable[ValidationCase]) -> list[ValidationCase]:
    return [c for c in cases if c.anti_overfit.used_for_training]


def holdout_cases(cases: Iterable[ValidationCase]) -> list[ValidationCase]:
    return [c for c in cases if c.anti_overfit.used_for_holdout]


def scored_cases(cases: Iterable[ValidationCase]) -> list[ValidationCase]:
    return [c for c in cases if c.metadata.validation_status == "scored"]


def ledger_summary(cases: list[ValidationCase]) -> dict[str, object]:
    """Aggregate avg MAE / TVD across scorable cases (measurement only)."""
    scorable = [c for c in cases if c.is_scorable()]
    maes: list[float] = []
    tvds: list[float] = []
    for c in scorable:
        m = compute_case_metrics(c)
        if m and m.mae_pp is not None:
            maes.append(m.mae_pp)
        if m and m.tvd is not None:
            tvds.append(m.tvd)
    return {
        "n_cases": len(cases),
        "n_scored": len(scored_cases(cases)),
        "n_scorable": len(scorable),
        "n_training": len(training_cases(cases)),
        "n_holdout": len(holdout_cases(cases)),
        "avg_mae_pp": round(sum(maes) / len(maes), 4) if maes else None,
        "avg_tvd": round(sum(tvds) / len(tvds), 4) if tvds else None,
    }
