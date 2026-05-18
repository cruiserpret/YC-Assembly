"""Phase 12A.2 — Score Assembly predictions against blinded cases.

This module is the **scoring surface**. It is the only place where:

  1. A :class:`BlindCase` is matched against an actual
     ``founder_report.json`` (or another prediction-artifact path).
  2. The hidden outcome is disclosed (via
     :meth:`BlindCase.read_outcome_for_scoring`, which itself refuses
     to disclose unless the prediction artifact already exists on disk).
  3. A calibration summary is computed.

The scoring functions never mutate the case or the prediction. They
read the artifact, compute, return. No DB writes, no LLM calls, no
HTTP.

Pack-level scoring aggregates per-case results into a calibration
roll-up: average MAE across cases, worst case, fraction of cases
that triggered false-confidence warnings. This is the primitive
that lets us judge whether Assembly is well-calibrated across a
*portfolio* of products, not just one.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from assembly.calibration.blind_case_schema import BlindCase
from assembly.calibration.case_pack_loader import CasePack
from assembly.calibration.distribution_metrics import (
    calibration_summary,
)
from assembly.calibration.report_extractor import (
    BucketCounts,
    extract_bucket_counts_from_founder_report,
)

logger = logging.getLogger(__name__)


ScoringStatus = Literal[
    "scored",
    "missing_prediction",
    "scoring_error",
    "blindness_violation",
]


@dataclass
class CaseScoringResult:
    """Per-case scoring result.

    The fields mirror the dict returned by
    :func:`distribution_metrics.calibration_summary` plus
    calibration-specific bookkeeping. Errors are reported in
    percentage points; TVD is reported as a fraction in [0, 1].
    """

    case_id: str
    scoring_status: ScoringStatus
    blindness_hash: str
    observed_sample_size: int
    predicted_distribution: dict[str, float] = field(default_factory=dict)
    observed_distribution: dict[str, float] = field(default_factory=dict)
    bucket_errors_pp: dict[str, float] = field(default_factory=dict)
    mean_absolute_bucket_error_pp: float | None = None
    max_bucket_error_pp: float | None = None
    total_variation_distance: float | None = None
    false_confidence_warnings: list[str] = field(default_factory=list)
    objection_recall: dict[str, Any] | None = None
    extractor_warnings: list[str] = field(default_factory=list)
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "scoring_status": self.scoring_status,
            "blindness_hash": self.blindness_hash,
            "observed_sample_size": self.observed_sample_size,
            "predicted_distribution": self.predicted_distribution,
            "observed_distribution": self.observed_distribution,
            "bucket_errors_pp": self.bucket_errors_pp,
            "mean_absolute_bucket_error_pp": (
                self.mean_absolute_bucket_error_pp
            ),
            "max_bucket_error_pp": self.max_bucket_error_pp,
            "total_variation_distance": self.total_variation_distance,
            "false_confidence_warnings": self.false_confidence_warnings,
            "objection_recall": self.objection_recall,
            "extractor_warnings": self.extractor_warnings,
            "error_message": self.error_message,
        }


# ---------------------------------------------------------------------------
# Per-case scoring
# ---------------------------------------------------------------------------


def score_blind_case_against_prediction(
    case: BlindCase,
    prediction_artifact_path: str | Path,
    *,
    objections_predicted: list[str] | None = None,
    payment_intent_explicit: bool = False,
) -> CaseScoringResult:
    """Score a single :class:`BlindCase` against the founder_report
    at ``prediction_artifact_path``.

    Returns a :class:`CaseScoringResult` whose ``scoring_status`` is:

      - ``"scored"``               — full calibration summary present
      - ``"missing_prediction"``    — the artifact file doesn't exist
      - ``"blindness_violation"``   — outcome can't be disclosed
                                      (e.g. cutoff invariant fails)
      - ``"scoring_error"``         — the artifact exists but
                                      bucket extraction failed

    Never raises for the missing-artifact / blindness-violation
    cases — those return a structured result so a pack-level run
    can carry on and surface the errors in aggregate.
    """
    case_id = case.pre_launch_input.case_id
    blindness_hash = case.compute_pre_launch_hash()
    observed_sample_size = (
        case.hidden_real_world_outcome.observed_sample_size
    )
    p = Path(prediction_artifact_path)

    # 1. Blindness gate: read_outcome_for_scoring will refuse if the
    #    prediction artifact doesn't exist or the cutoff invariant
    #    fails. We catch and surface both as structured statuses.
    try:
        outcome = case.read_outcome_for_scoring(
            prediction_artifact_path=p,
        )
    except FileNotFoundError as e:
        return CaseScoringResult(
            case_id=case_id,
            scoring_status="missing_prediction",
            blindness_hash=blindness_hash,
            observed_sample_size=observed_sample_size,
            error_message=str(e),
        )
    except RuntimeError as e:
        # _OutcomeNotYetReadableError subclasses RuntimeError.
        return CaseScoringResult(
            case_id=case_id,
            scoring_status="missing_prediction",
            blindness_hash=blindness_hash,
            observed_sample_size=observed_sample_size,
            error_message=str(e),
        )
    except ValueError as e:
        return CaseScoringResult(
            case_id=case_id,
            scoring_status="blindness_violation",
            blindness_hash=blindness_hash,
            observed_sample_size=observed_sample_size,
            error_message=str(e),
        )

    # 2. Extract predicted bucket counts from the artifact.
    try:
        predicted_counts: BucketCounts = (
            extract_bucket_counts_from_founder_report(
                p,
                payment_intent_explicit=payment_intent_explicit,
            )
        )
    except (ValueError, FileNotFoundError) as e:
        return CaseScoringResult(
            case_id=case_id,
            scoring_status="scoring_error",
            blindness_hash=blindness_hash,
            observed_sample_size=observed_sample_size,
            error_message=f"extraction failed: {e}",
        )

    # 3. Compute calibration summary in percent-point space.
    summary = calibration_summary(
        predicted_counts.as_dict(),
        outcome.observed_distribution,
        mode="percent",
        objections_predicted=objections_predicted,
        objections_observed=(
            list(outcome.observed_objections)
            if outcome.observed_objections is not None else None
        ),
    )

    return CaseScoringResult(
        case_id=case_id,
        scoring_status="scored",
        blindness_hash=blindness_hash,
        observed_sample_size=observed_sample_size,
        predicted_distribution=summary["predicted_distribution"],
        observed_distribution=summary["observed_distribution"],
        bucket_errors_pp=summary["bucket_errors"],
        mean_absolute_bucket_error_pp=(
            summary["mean_absolute_bucket_error"]
        ),
        max_bucket_error_pp=summary["max_bucket_error"],
        total_variation_distance=summary["total_variation_distance"],
        false_confidence_warnings=summary["false_confidence_warnings"],
        objection_recall=summary.get("objection_recall"),
        extractor_warnings=list(predicted_counts.warnings),
    )


# ---------------------------------------------------------------------------
# Pack-level scoring
# ---------------------------------------------------------------------------


def score_case_pack(
    case_pack: CasePack,
    prediction_artifact_paths_by_case_id: Mapping[str, str | Path],
    *,
    objections_predicted_by_case_id: Mapping[str, list[str]] | None = None,
    payment_intent_explicit_by_case_id: Mapping[str, bool] | None = None,
) -> list[CaseScoringResult]:
    """Score every case in ``case_pack`` against its prediction
    artifact (looked up by ``case_id``).

    Missing prediction artifacts produce a ``missing_prediction``
    result rather than an exception — that's the right behavior for
    a calibration sweep where some cases may not yet have an
    Assembly run.
    """
    results: list[CaseScoringResult] = []
    objs = objections_predicted_by_case_id or {}
    pay_intent = payment_intent_explicit_by_case_id or {}
    for case_id, case in case_pack.cases.items():
        artifact_path = prediction_artifact_paths_by_case_id.get(case_id)
        if artifact_path is None:
            results.append(
                CaseScoringResult(
                    case_id=case_id,
                    scoring_status="missing_prediction",
                    blindness_hash=case.compute_pre_launch_hash(),
                    observed_sample_size=(
                        case.hidden_real_world_outcome.observed_sample_size
                    ),
                    error_message=(
                        f"no prediction artifact path provided for "
                        f"case_id={case_id!r}"
                    ),
                )
            )
            continue
        results.append(score_blind_case_against_prediction(
            case,
            artifact_path,
            objections_predicted=objs.get(case_id),
            payment_intent_explicit=pay_intent.get(case_id, False),
        ))
    return results


# ---------------------------------------------------------------------------
# Roll-up across a pack
# ---------------------------------------------------------------------------


def summarize_case_pack_scores(
    results: list[CaseScoringResult],
) -> dict[str, Any]:
    """Reduce per-case results into a single calibration report.

    Returns a dict with:
      - case_count                     — how many cases were attempted
      - scored_count                   — how many produced a full summary
      - missing_prediction_count
      - blindness_violation_count
      - scoring_error_count
      - average_mae_pp                 — mean of per-case MAE (scored only)
      - worst_mae_pp + worst_case_id
      - average_max_bucket_error_pp
      - average_tvd
      - case_with_critical_warnings    — list of (case_id, warning_count)
      - per_case                       — list of result.as_dict()
    """
    scored = [r for r in results if r.scoring_status == "scored"]
    case_count = len(results)
    if scored:
        avg_mae = sum(
            r.mean_absolute_bucket_error_pp or 0 for r in scored
        ) / len(scored)
        worst = max(
            scored,
            key=lambda r: r.mean_absolute_bucket_error_pp or 0,
        )
        avg_max_err = sum(
            r.max_bucket_error_pp or 0 for r in scored
        ) / len(scored)
        avg_tvd = sum(
            r.total_variation_distance or 0 for r in scored
        ) / len(scored)
    else:
        avg_mae = None
        worst = None
        avg_max_err = None
        avg_tvd = None

    critical: list[tuple[str, int]] = []
    for r in scored:
        crit = [
            w for w in r.false_confidence_warnings
            if "_critical" in w
        ]
        if crit:
            critical.append((r.case_id, len(crit)))

    return {
        "case_count": case_count,
        "scored_count": len(scored),
        "missing_prediction_count": sum(
            1 for r in results if r.scoring_status == "missing_prediction"
        ),
        "blindness_violation_count": sum(
            1 for r in results if r.scoring_status == "blindness_violation"
        ),
        "scoring_error_count": sum(
            1 for r in results if r.scoring_status == "scoring_error"
        ),
        "average_mae_pp": avg_mae,
        "worst_mae_pp": (
            worst.mean_absolute_bucket_error_pp if worst else None
        ),
        "worst_case_id": worst.case_id if worst else None,
        "average_max_bucket_error_pp": avg_max_err,
        "average_tvd": avg_tvd,
        "cases_with_critical_warnings": critical,
        "per_case": [r.as_dict() for r in results],
    }
