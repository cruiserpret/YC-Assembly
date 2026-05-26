"""Phase 12A.9 — Operator-labeled outcome ingestion + blind scoring.

This module is the **scoring side** of the calibration loop. It
takes operator-supplied real-world labels (one bucket assignment
per usable comment from a public outcome source), produces an
observed distribution, hashes the prediction artifact for tamper
detection, and scores Assembly's locked prediction against the
observed reality.

What this module is NOT:

  - It does NOT call any LLM. Scoring is pure deterministic math
    over Phase 12A.1 calibration primitives.
  - It does NOT scrape, fetch, or call any API.
  - It does NOT mutate the prediction artifact. The artifact path
    is read-only; ``sha256_of_prediction_artifact`` is invoked
    before AND after scoring, asserted equal.
  - It does NOT rerun Assembly. There is no path here that touches
    the orchestration / discussion / persona pipelines.
  - It does NOT fabricate labels. If the operator's labeled file is
    missing or empty, the workflow refuses to produce a score.

Honesty rule:
  Per the Phase 12A.9 spec — "Do not protect Assembly's ego. The
  goal is to find the truth." If the operator's labels show
  Assembly was wrong, the scoring output says so verbatim. Nothing
  in this module suppresses or smooths the error signal.

The label vocabulary is closed:
  buyer / receptive / uncertain / skeptical / noise

``noise`` is excluded from ``observed_sample_size`` — pure
congratulations, jokes, off-topic, duplicate low-content comments,
self-promotion, and irrelevant side threads. Only the four
calibration buckets enter the percent distribution.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from assembly.calibration.distribution_metrics import (
    bucket_absolute_errors,
    calibration_summary,
    max_bucket_error,
    mean_absolute_bucket_error,
    total_variation_distance,
)
from assembly.calibration.market_buckets import BUCKET_NAMES, MarketBucket
from assembly.calibration.report_extractor import (
    extract_bucket_counts_from_founder_report,
)

logger = logging.getLogger(__name__)


OutcomeLabel = Literal[
    "buyer", "receptive", "uncertain", "skeptical", "noise",
]

_VALID_LABELS: frozenset[str] = frozenset(
    ("buyer", "receptive", "uncertain", "skeptical", "noise")
)

# Tolerated alternative spellings the operator might use. Mapped at
# parse time to the canonical label. Kept narrow on purpose — any
# value that's not in this map or the canonical set is a hard error.
_LABEL_ALIASES: dict[str, OutcomeLabel] = {
    "buy": "buyer",
    "would_buy": "buyer",
    "adopter": "buyer",
    "consider": "receptive",
    "interested": "receptive",
    "unsure": "uncertain",
    "wait_and_see": "uncertain",
    "neutral": "uncertain",
    "reject": "skeptical",
    "would_reject": "skeptical",
    "loyal": "skeptical",
    "drop": "noise",
    "noise/drop": "noise",
    "skip": "noise",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LabeledOutcomeRow:
    """One labeled comment from the operator's outcome file."""

    comment_id: str
    label: OutcomeLabel
    excerpt: str = ""
    objection_tags: list[str] = field(default_factory=list)
    labeler_notes: str = ""


@dataclass
class LabeledOutcomeFile:
    """Parsed + validated outcome file.

    Phase 12E.fix2 — `observed_collection_date` is now OPTIONAL. When
    missing from the label file, downstream callers must treat it as
    "unknown" (rendered literally as `unknown` in audit markdown) and
    skip the cutoff-date strictness check. Pre-12E.fix2 the parser
    raised on missing dates, which caused the variance harness to
    abort after a successful pipeline run.
    """

    rows: list[LabeledOutcomeRow]
    observed_collection_date: date | None
    cutoff_date: date
    parse_warnings: list[str] = field(default_factory=list)
    observed_objections: list[str] = field(default_factory=list)
    labeler_notes_summary: str = ""

    def usable_rows(self) -> list[LabeledOutcomeRow]:
        """Rows whose label is one of the 4 calibration buckets
        (i.e., excluding ``noise``)."""
        return [r for r in self.rows if r.label != "noise"]

    def noise_rows(self) -> list[LabeledOutcomeRow]:
        return [r for r in self.rows if r.label == "noise"]


@dataclass
class ObservedDistribution:
    """Bucket counts + percents after dropping noise."""

    buyer: int = 0
    receptive: int = 0
    uncertain: int = 0
    skeptical: int = 0
    noise: int = 0

    @property
    def observed_sample_size(self) -> int:
        """The count we score against — noise excluded."""
        return self.buyer + self.receptive + self.uncertain + self.skeptical

    def as_counts(self) -> dict[MarketBucket, int]:
        return {
            "buyer": self.buyer,
            "receptive": self.receptive,
            "uncertain": self.uncertain,
            "skeptical": self.skeptical,
        }

    def as_percent(self) -> dict[MarketBucket, float]:
        n = self.observed_sample_size
        if n == 0:
            return {b: 0.0 for b in BUCKET_NAMES}
        return {
            "buyer": 100.0 * self.buyer / n,
            "receptive": 100.0 * self.receptive / n,
            "uncertain": 100.0 * self.uncertain / n,
            "skeptical": 100.0 * self.skeptical / n,
        }


@dataclass
class BlindScoringResult:
    """Final scoring output. All percentage-point errors are in
    the same units as the predicted distribution
    (percent → percentage points)."""

    candidate_id: str
    cutoff_date: date
    # Phase 12E.fix2 — optional; renders as "unknown" in audit when
    # the label file omitted observed_collection_date.
    observed_collection_date: date | None
    prediction_artifact_path: str
    prediction_artifact_hash_before: str
    prediction_artifact_hash_after: str
    prediction_artifact_hash_unchanged: bool

    predicted_distribution_percent: dict[MarketBucket, float]
    observed_distribution_percent: dict[MarketBucket, float]
    predicted_counts: dict[MarketBucket, int]
    observed_counts: dict[MarketBucket, int]

    observed_sample_size: int
    noise_dropped_count: int

    signed_bucket_errors_pp: dict[MarketBucket, float]
    absolute_bucket_errors_pp: dict[MarketBucket, float]
    mean_absolute_bucket_error_pp: float
    max_bucket_error_pp: float
    total_variation_distance: float
    false_confidence_warnings: list[str]
    objection_recall: dict[str, Any] | None = None

    interpretation_band: str = "unknown"
    labeler_notes_summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": "12a_9_blind_outcome_scoring",
            "candidate_id": self.candidate_id,
            "cutoff_date": self.cutoff_date.isoformat(),
            "observed_collection_date": (
                self.observed_collection_date.isoformat()
                if self.observed_collection_date is not None
                else "unknown"
            ),
            "prediction_artifact_path": self.prediction_artifact_path,
            "prediction_artifact_hash_before": (
                self.prediction_artifact_hash_before
            ),
            "prediction_artifact_hash_after": (
                self.prediction_artifact_hash_after
            ),
            "prediction_artifact_hash_unchanged": (
                self.prediction_artifact_hash_unchanged
            ),
            "predicted_distribution_percent": (
                self.predicted_distribution_percent
            ),
            "observed_distribution_percent": (
                self.observed_distribution_percent
            ),
            "predicted_counts": self.predicted_counts,
            "observed_counts": self.observed_counts,
            "observed_sample_size": self.observed_sample_size,
            "noise_dropped_count": self.noise_dropped_count,
            "signed_bucket_errors_pp": self.signed_bucket_errors_pp,
            "absolute_bucket_errors_pp": self.absolute_bucket_errors_pp,
            "mean_absolute_bucket_error_pp": (
                self.mean_absolute_bucket_error_pp
            ),
            "max_bucket_error_pp": self.max_bucket_error_pp,
            "total_variation_distance": self.total_variation_distance,
            "false_confidence_warnings": (
                self.false_confidence_warnings
            ),
            "objection_recall": self.objection_recall,
            "interpretation_band": self.interpretation_band,
            "labeler_notes_summary": self.labeler_notes_summary,
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OutcomeLabelingError(ValueError):
    """Raised when the operator's labeled file is malformed or
    violates an invariant. Carries a list[str] of violations."""

    def __init__(self, message: str, violations: list[str] | None = None):
        super().__init__(message)
        self.violations: list[str] = list(violations or [])


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _normalize_label(raw: Any) -> OutcomeLabel:
    if raw is None:
        raise OutcomeLabelingError("label_is_none")
    if not isinstance(raw, str):
        raise OutcomeLabelingError(
            f"label_not_string: {raw!r} (type {type(raw).__name__})"
        )
    norm = raw.strip().lower().replace("-", "_")
    if norm in _VALID_LABELS:
        return norm  # type: ignore[return-value]
    if norm in _LABEL_ALIASES:
        return _LABEL_ALIASES[norm]
    raise OutcomeLabelingError(
        f"invalid_label={raw!r}: must be one of "
        f"{sorted(_VALID_LABELS)} (aliases accepted: "
        f"{sorted(_LABEL_ALIASES)})"
    )


def _coerce_date(raw: Any, *, field_name: str) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        s = raw.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    raise OutcomeLabelingError(
        f"unparseable_{field_name}={raw!r}: expected ISO YYYY-MM-DD"
    )


def parse_labeled_outcome_file(
    path: str | Path,
    *,
    cutoff_date: date,
) -> LabeledOutcomeFile:
    """Parse and validate an operator-supplied labeled-outcome file.

    Accepts:
      - JSON: ``{"observed_collection_date": "YYYY-MM-DD",
                  "observed_objections": [...],
                  "labeler_notes_summary": "...",
                  "rows": [{comment_id, label, excerpt,
                            objection_tags, labeler_notes}, ...]}``
      - CSV:  columns: comment_id, label, excerpt (optional),
              objection_tags (optional, semicolon-separated),
              labeler_notes (optional). Plus a header row labeled
              ``# observed_collection_date=YYYY-MM-DD`` immediately
              before the data rows.

    Validates:
      - every row label is one of the closed set
      - no duplicate comment_id
      - observed_collection_date strictly after ``cutoff_date``
      - file is non-empty

    Returns a :class:`LabeledOutcomeFile`. Raises
    :class:`OutcomeLabelingError` on any structural failure.
    """
    p = Path(path)
    if not p.exists():
        raise OutcomeLabelingError(
            f"outcome_file_not_found: {p!s}"
        )
    if p.suffix.lower() == ".json":
        return _parse_json(p, cutoff_date=cutoff_date)
    if p.suffix.lower() == ".csv":
        return _parse_csv(p, cutoff_date=cutoff_date)
    raise OutcomeLabelingError(
        f"unsupported_outcome_file_extension={p.suffix!r}: "
        "must be .json or .csv"
    )


def _parse_json(p: Path, *, cutoff_date: date) -> LabeledOutcomeFile:
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise OutcomeLabelingError(
            f"outcome_file_malformed_json: {e}"
        ) from e
    if not isinstance(payload, dict):
        raise OutcomeLabelingError(
            f"outcome_file_top_level_not_dict: got {type(payload).__name__}"
        )
    # Phase 12E.fix2 — observed_collection_date is now optional.
    # When omitted, we record a warning and skip the cutoff-date
    # strictness check so downstream scoring + markdown rendering
    # can degrade gracefully (renders as "unknown").
    raw_date = payload.get("observed_collection_date")
    obs_date: date | None
    if not raw_date:
        obs_date = None
    else:
        obs_date = _coerce_date(
            raw_date, field_name="observed_collection_date",
        )
        if obs_date <= cutoff_date:
            raise OutcomeLabelingError(
                f"observed_collection_date={obs_date.isoformat()} "
                f"not strictly after cutoff_date={cutoff_date.isoformat()}"
            )
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or len(raw_rows) == 0:
        raise OutcomeLabelingError(
            "outcome_file_has_no_rows"
        )

    rows: list[LabeledOutcomeRow] = []
    warnings: list[str] = []
    violations: list[str] = []
    seen_ids: set[str] = set()
    if obs_date is None:
        warnings.append(
            "missing_observed_collection_date_defaulted_to_unknown"
        )

    for i, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            violations.append(
                f"row[{i}]_not_a_dict (type {type(raw).__name__})"
            )
            continue
        cid = str(raw.get("comment_id") or "").strip()
        if not cid:
            violations.append(f"row[{i}]_missing_comment_id")
            continue
        if cid in seen_ids:
            violations.append(f"duplicate_comment_id={cid!r}")
            continue
        seen_ids.add(cid)
        try:
            label = _normalize_label(raw.get("label"))
        except OutcomeLabelingError as e:
            violations.append(
                f"row[{i}] comment_id={cid!r}: {e}"
            )
            continue
        tags_raw = raw.get("objection_tags") or []
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.split(";") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        else:
            warnings.append(
                f"row[{i}]_objection_tags_unsupported_type={type(tags_raw).__name__}"
            )
            tags = []
        rows.append(
            LabeledOutcomeRow(
                comment_id=cid,
                label=label,
                excerpt=str(raw.get("excerpt") or ""),
                objection_tags=tags,
                labeler_notes=str(raw.get("labeler_notes") or ""),
            )
        )

    if violations:
        raise OutcomeLabelingError(
            "outcome_file_validation_failed",
            violations=violations,
        )
    observed_objections_raw = payload.get("observed_objections") or []
    if not isinstance(observed_objections_raw, list):
        warnings.append(
            "observed_objections_not_a_list_dropping"
        )
        observed_objections_raw = []
    observed_objections = [
        str(o).strip() for o in observed_objections_raw
        if str(o).strip()
    ]
    labeler_notes_summary = str(
        payload.get("labeler_notes_summary") or ""
    ).strip()

    return LabeledOutcomeFile(
        rows=rows,
        observed_collection_date=obs_date,
        cutoff_date=cutoff_date,
        parse_warnings=warnings,
        observed_objections=observed_objections,
        labeler_notes_summary=labeler_notes_summary,
    )


def _parse_csv(p: Path, *, cutoff_date: date) -> LabeledOutcomeFile:
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    obs_date: date | None = None
    body_lines: list[str] = []
    for line in lines:
        if line.startswith("# observed_collection_date="):
            obs_date = _coerce_date(
                line.split("=", 1)[1].strip(),
                field_name="observed_collection_date",
            )
        elif not line.startswith("#"):
            body_lines.append(line)
    # Phase 12E.fix2 — observed_collection_date header is optional.
    # When missing we degrade gracefully; downstream renderer prints
    # `unknown` and the cutoff-date strictness check is skipped.
    if obs_date is not None and obs_date <= cutoff_date:
        raise OutcomeLabelingError(
            f"observed_collection_date={obs_date.isoformat()} "
            f"not strictly after cutoff_date={cutoff_date.isoformat()}"
        )
    if not body_lines:
        raise OutcomeLabelingError("csv_has_no_body_rows")
    reader = csv.DictReader(body_lines)
    rows: list[LabeledOutcomeRow] = []
    warnings: list[str] = []
    violations: list[str] = []
    seen_ids: set[str] = set()
    if obs_date is None:
        warnings.append(
            "missing_observed_collection_date_defaulted_to_unknown"
        )
    for i, raw in enumerate(reader):
        cid = (raw.get("comment_id") or "").strip()
        if not cid:
            violations.append(f"row[{i}]_missing_comment_id")
            continue
        if cid in seen_ids:
            violations.append(f"duplicate_comment_id={cid!r}")
            continue
        seen_ids.add(cid)
        try:
            label = _normalize_label(raw.get("label"))
        except OutcomeLabelingError as e:
            violations.append(
                f"row[{i}] comment_id={cid!r}: {e}"
            )
            continue
        tags_raw = (raw.get("objection_tags") or "").strip()
        tags = [t.strip() for t in tags_raw.split(";") if t.strip()]
        rows.append(
            LabeledOutcomeRow(
                comment_id=cid,
                label=label,
                excerpt=(raw.get("excerpt") or "").strip(),
                objection_tags=tags,
                labeler_notes=(raw.get("labeler_notes") or "").strip(),
            )
        )
    if violations:
        raise OutcomeLabelingError(
            "outcome_csv_validation_failed",
            violations=violations,
        )
    return LabeledOutcomeFile(
        rows=rows,
        observed_collection_date=obs_date,
        cutoff_date=cutoff_date,
        parse_warnings=warnings,
        observed_objections=[],
        labeler_notes_summary="",
    )


# ---------------------------------------------------------------------------
# Observed distribution
# ---------------------------------------------------------------------------


def compute_observed_distribution(
    labeled: LabeledOutcomeFile,
) -> ObservedDistribution:
    """Reduce a labeled file to an :class:`ObservedDistribution`.

    Noise rows are counted in ``noise`` but excluded from the
    four calibration buckets and from ``observed_sample_size``.
    """
    out = ObservedDistribution()
    for r in labeled.rows:
        if r.label == "buyer":
            out.buyer += 1
        elif r.label == "receptive":
            out.receptive += 1
        elif r.label == "uncertain":
            out.uncertain += 1
        elif r.label == "skeptical":
            out.skeptical += 1
        elif r.label == "noise":
            out.noise += 1
    return out


# ---------------------------------------------------------------------------
# Prediction artifact hash + load
# ---------------------------------------------------------------------------


def sha256_of_prediction_artifact(path: str | Path) -> str:
    """sha256 of the prediction artifact's bytes on disk.

    Used for tamper detection: ``score_blind_outcome`` computes
    this BEFORE and AFTER the scoring math and asserts equal. Any
    code path that mutates the artifact (no such path exists in
    Phase 12A.9, by design) would be caught.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"prediction artifact not found: {p!s}"
        )
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


_STRICT_MAE_THRESHOLD_PP = 8.0
_STRICT_MAX_ERR_THRESHOLD_PP = 15.0
_PROMISING_MAE_CEILING_PP = 12.0
_PROMISING_MAX_ERR_CEILING_PP = 20.0
_PROBLEM_MAE_FLOOR_PP = 12.0
_PROBLEM_MAX_ERR_FLOOR_PP = 25.0


def _interpretation_band(
    mae_pp: float,
    max_err_pp: float,
    false_confidence_warnings: list[str],
) -> str:
    has_critical = any("_critical" in w for w in false_confidence_warnings)
    if (
        mae_pp <= _STRICT_MAE_THRESHOLD_PP
        and max_err_pp <= _STRICT_MAX_ERR_THRESHOLD_PP
        and not has_critical
    ):
        return "strict_success"
    if (
        mae_pp <= _PROMISING_MAE_CEILING_PP
        and max_err_pp <= _PROMISING_MAX_ERR_CEILING_PP
    ):
        return "promising_needs_calibration"
    if (
        mae_pp > _PROBLEM_MAE_FLOOR_PP
        or max_err_pp > _PROBLEM_MAX_ERR_FLOOR_PP
    ):
        return "problem_fix_before_next_case"
    return "between_bands"


def score_blind_outcome(
    *,
    candidate_id: str,
    prediction_artifact_path: str | Path,
    labeled_outcome: LabeledOutcomeFile,
    objections_predicted: list[str] | None = None,
) -> BlindScoringResult:
    """Score Assembly's locked prediction against operator labels.

    Process:
      1. Hash the prediction artifact (BEFORE).
      2. Extract Assembly's predicted bucket counts via the
         Phase 12A.1 report extractor (read-only access).
      3. Compute observed distribution from operator labels
         (noise excluded).
      4. Compute per-bucket signed and absolute errors, MAE,
         max-error, TVD, false-confidence warnings, objection
         recall.
      5. Hash the prediction artifact (AFTER). Assert equal to BEFORE.
      6. Assign an interpretation band against Phase 12A.9 thresholds.

    Raises ``OutcomeLabelingError`` if the prediction artifact is
    missing or its hash changes during scoring.
    """
    artifact_path = Path(prediction_artifact_path)
    if not artifact_path.exists():
        raise OutcomeLabelingError(
            f"prediction_artifact_missing: {artifact_path!s}"
        )

    # 1. Hash before
    hash_before = sha256_of_prediction_artifact(artifact_path)

    # 2. Predicted distribution from artifact (read-only)
    predicted_counts_obj = extract_bucket_counts_from_founder_report(
        artifact_path,
    )
    predicted_counts = predicted_counts_obj.as_dict()
    predicted_dist = predicted_counts_obj.as_distribution()
    predicted_percent = {
        b: 100.0 * predicted_dist[b] for b in BUCKET_NAMES
    }

    # 3. Observed distribution from labels
    observed = compute_observed_distribution(labeled_outcome)
    if observed.observed_sample_size == 0:
        raise OutcomeLabelingError(
            "observed_sample_size_is_zero — every row was labeled "
            "as noise, refusing to score against an empty distribution"
        )
    observed_counts = observed.as_counts()
    observed_percent = observed.as_percent()

    # 4. Errors + summary
    abs_errors = bucket_absolute_errors(
        predicted_percent, observed_percent, mode="percent",
    )
    signed_errors = {
        b: predicted_percent[b] - observed_percent[b]
        for b in BUCKET_NAMES
    }
    mae_pp = mean_absolute_bucket_error(
        predicted_percent, observed_percent, mode="percent",
    )
    max_err_pp = max_bucket_error(
        predicted_percent, observed_percent, mode="percent",
    )
    tvd = total_variation_distance(
        predicted_percent, observed_percent, mode="percent",
    )
    # Objection recall semantics:
    #   - If the operator supplied observed_objections, compute recall.
    #   - If the caller did not pass objections_predicted, treat
    #     predicted as the empty list so recall is 0.0 with the full
    #     observed list in `missed` (useful diagnostic — surfaces
    #     every objection Assembly's prediction failed to anticipate).
    obs_obj_for_recall = (
        labeled_outcome.observed_objections
        if labeled_outcome.observed_objections else None
    )
    pred_obj_for_recall: list[str] | None
    if obs_obj_for_recall is not None:
        pred_obj_for_recall = list(objections_predicted or [])
    else:
        pred_obj_for_recall = (
            list(objections_predicted)
            if objections_predicted is not None else None
        )
    summary = calibration_summary(
        predicted_percent,
        observed_percent,
        mode="percent",
        objections_predicted=pred_obj_for_recall,
        objections_observed=obs_obj_for_recall,
    )
    false_confidence_warnings = list(
        summary["false_confidence_warnings"]
    )
    objection_recall = summary.get("objection_recall")

    # 5. Hash after — must match (tamper detection)
    hash_after = sha256_of_prediction_artifact(artifact_path)
    unchanged = hash_before == hash_after
    if not unchanged:
        # We do not raise here because the scoring math is already
        # done and we want to record the violation in the result;
        # the caller is expected to halt on the recorded mismatch.
        logger.error(
            "calibration.phase_12a_9 prediction artifact hash "
            "changed during scoring before=%s after=%s",
            hash_before, hash_after,
        )

    # 6. Interpretation band
    band = _interpretation_band(
        mae_pp, max_err_pp, false_confidence_warnings,
    )

    return BlindScoringResult(
        candidate_id=candidate_id,
        cutoff_date=labeled_outcome.cutoff_date,
        observed_collection_date=labeled_outcome.observed_collection_date,
        prediction_artifact_path=str(artifact_path),
        prediction_artifact_hash_before=hash_before,
        prediction_artifact_hash_after=hash_after,
        prediction_artifact_hash_unchanged=unchanged,
        predicted_distribution_percent=predicted_percent,
        observed_distribution_percent=observed_percent,
        predicted_counts=predicted_counts,
        observed_counts=observed_counts,
        observed_sample_size=observed.observed_sample_size,
        noise_dropped_count=observed.noise,
        signed_bucket_errors_pp=signed_errors,
        absolute_bucket_errors_pp=abs_errors,
        mean_absolute_bucket_error_pp=mae_pp,
        max_bucket_error_pp=max_err_pp,
        total_variation_distance=tvd,
        false_confidence_warnings=false_confidence_warnings,
        objection_recall=objection_recall,
        interpretation_band=band,
        labeler_notes_summary=labeled_outcome.labeler_notes_summary,
    )


# ---------------------------------------------------------------------------
# Audit artifact emission
# ---------------------------------------------------------------------------


def write_phase_12a_9_audit(
    result: BlindScoringResult,
    *,
    json_path: str | Path,
    md_path: str | Path,
) -> None:
    """Write a JSON + Markdown audit artifact. Pure file write —
    no DB, no network."""
    jp = Path(json_path)
    mp = Path(md_path)
    jp.parent.mkdir(parents=True, exist_ok=True)
    mp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(
        json.dumps(result.as_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    mp.write_text(_render_md(result), encoding="utf-8")


def _render_md(r: BlindScoringResult) -> str:
    lines: list[str] = []
    lines.append(
        f"# Phase 12A.9 — Blind Outcome Score: {r.candidate_id}"
    )
    lines.append("")
    lines.append(
        f"**Interpretation band:** `{r.interpretation_band}`"
    )
    lines.append("")
    lines.append(
        f"- prediction artifact: `{r.prediction_artifact_path}`"
    )
    lines.append(
        f"- hash before:  `{r.prediction_artifact_hash_before}`"
    )
    lines.append(
        f"- hash after:   `{r.prediction_artifact_hash_after}`"
    )
    lines.append(
        f"- hash unchanged: **{r.prediction_artifact_hash_unchanged}**"
    )
    lines.append(
        f"- observed_sample_size: {r.observed_sample_size}"
    )
    lines.append(
        f"- noise dropped: {r.noise_dropped_count}"
    )
    lines.append(
        f"- cutoff_date: {r.cutoff_date.isoformat()}"
    )
    # Phase 12E.fix2 — observed_collection_date is optional.
    lines.append(
        f"- observed_collection_date: "
        f"{r.observed_collection_date.isoformat() if r.observed_collection_date is not None else 'unknown'}"
    )
    lines.append("")
    lines.append("## Predicted vs Observed (percent)")
    lines.append("")
    lines.append("| bucket | predicted | observed | signed err (pp) | abs err (pp) |")
    lines.append("|---|---:|---:|---:|---:|")
    for b in BUCKET_NAMES:
        lines.append(
            f"| {b} "
            f"| {r.predicted_distribution_percent[b]:.2f} "
            f"| {r.observed_distribution_percent[b]:.2f} "
            f"| {r.signed_bucket_errors_pp[b]:+.2f} "
            f"| {r.absolute_bucket_errors_pp[b]:.2f} |"
        )
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    lines.append(
        f"- **MAE**: {r.mean_absolute_bucket_error_pp:.2f} pp"
    )
    lines.append(
        f"- **Max bucket error**: {r.max_bucket_error_pp:.2f} pp"
    )
    lines.append(
        f"- **Total Variation Distance**: {r.total_variation_distance:.4f}"
    )
    lines.append("")
    lines.append("## False-confidence warnings")
    lines.append("")
    if r.false_confidence_warnings:
        for w in r.false_confidence_warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- (none)")
    lines.append("")
    if r.objection_recall:
        lines.append("## Objection recall")
        lines.append("")
        lines.append(f"- recall: {r.objection_recall.get('recall')}")
        lines.append(
            f"- matched: {r.objection_recall.get('matched')}"
        )
        lines.append(
            f"- missed: {r.objection_recall.get('missed')}"
        )
        lines.append("")
    if r.labeler_notes_summary:
        lines.append("## Labeler notes summary")
        lines.append("")
        lines.append(r.labeler_notes_summary)
        lines.append("")
    return "\n".join(lines)
