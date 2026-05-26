"""Phase 12E.5D — durable scoring helpers for the variance harness.

Pure module. No DB, no LLM, no network. Designed to be importable by:
  - The ad-hoc variance harness at /tmp/phase_12a_10c_repeatability_harness.py
  - Any future operator script that scores a paid run against labels
  - The Proof Packet builder

Surfaces:
  * `verify_labels_file_or_raise(path)` — preflight guard that fails
    LOUDLY before any paid run if a requested labels file is missing
    or malformed (would later silently skip scoring under the old
    harness logic).
  * `copy_labels_into_batch_dir(labels_src, batch_dir)` — copy the
    labels file into the batch audit directory at launch time + emit
    its sha256 hash. The copy + hash are the durability guarantee:
    subsequent scoring uses the COPY (not the original path), so
    /tmp cleaning or operator edits cannot invalidate the run.
  * `score_run_against_durable_labels(...)` — given a per-run
    predicted distribution + the durable labels path, run the scorer
    end-to-end and return a JSON-safe metrics dict.
  * `extract_skeptic_retention_from_diversity_health(...)` — pillar
    extractor that copes with both `skeptic_retention` AND
    `skeptic_retention_rate` field names (Phase 12C used the latter;
    the pillar contract used the former).

Threat model + invariants:
  - If `--score-vs-labels` was requested, scoring MUST run; failures
    write a `scoring_error` dict into the per-run metrics so the
    batch summary surfaces the gap.
  - Labels copy lives in the batch audit dir under
    `labels_used/<original_filename>` so the link to per-run scoring
    is unambiguous.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LabelsFileError(Exception):
    """Raised when a labels file is missing, unreadable, or malformed."""


# ---------------------------------------------------------------------------
# Preflight + durability
# ---------------------------------------------------------------------------


@dataclass
class DurableLabels:
    """The result of copying a labels file into the batch audit dir."""

    original_path: Path
    durable_path: Path  # inside batch_dir/labels_used/
    sha256: str
    n_bytes: int


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_labels_file_or_raise(labels_path: str | Path) -> Path:
    """Preflight guard. Raises LabelsFileError if the path is missing,
    unreadable, or not parseable as a JSON object with at minimum a
    `rows` list. Returns the Path on success.

    Called at the START of the harness when --score-vs-labels is
    supplied, BEFORE any paid LLM calls.
    """
    p = Path(labels_path)
    if not p.exists():
        raise LabelsFileError(
            f"labels file not found: {p}. "
            "Refusing to launch a paid run with --score-vs-labels set "
            "to a missing path."
        )
    if not p.is_file():
        raise LabelsFileError(
            f"labels path is not a file: {p}"
        )
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise LabelsFileError(
            f"labels file unreadable: {p}: {e}"
        ) from e
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise LabelsFileError(
            f"labels file malformed JSON: {p}: {e}"
        ) from e
    if not isinstance(payload, dict):
        raise LabelsFileError(
            f"labels file top-level not a dict: {p} "
            f"(got {type(payload).__name__})"
        )
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) == 0:
        raise LabelsFileError(
            f"labels file has no `rows` list or it is empty: {p}"
        )
    return p


def copy_labels_into_batch_dir(
    *,
    labels_src: str | Path,
    batch_dir: str | Path,
) -> DurableLabels:
    """Copy the labels file into `batch_dir/labels_used/<filename>`
    and emit its sha256. The copy is the durable scoring source going
    forward; the original `labels_src` path is recorded for audit.

    The caller is responsible for storing the resulting
    `DurableLabels` reference (typically into runtime_config_batch.json).
    """
    src = verify_labels_file_or_raise(labels_src)
    out_dir = Path(batch_dir) / "labels_used"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / src.name
    shutil.copy2(src, dest)
    return DurableLabels(
        original_path=src.resolve(),
        durable_path=dest.resolve(),
        sha256=_sha256_file(dest),
        n_bytes=dest.stat().st_size,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_run_against_durable_labels(
    *,
    predicted_pct: dict[str, float],
    durable_labels_path: str | Path,
    cutoff_date: date,
) -> dict[str, Any]:
    """Score a single run's `predicted_pct` against a durable labels
    file. Returns a dict with `observed_pct`, `mae_pp`, `tvd`,
    `signed_err_pp`, `abs_err_pp`, `max_err_pp`. Pure, no I/O beyond
    reading the labels file.

    Raises LabelsFileError on missing / malformed labels (after the
    preflight, this should not fire; the second guard catches an
    operator mistake mid-batch — e.g. deleting the durable copy).
    """
    from assembly.calibration import (
        compute_observed_distribution,
        parse_labeled_outcome_file,
    )
    p = Path(durable_labels_path)
    if not p.exists():
        raise LabelsFileError(
            f"durable labels file disappeared between preflight and "
            f"scoring: {p}"
        )
    outcome = parse_labeled_outcome_file(p, cutoff_date=cutoff_date)
    observed = compute_observed_distribution(outcome)
    obs_pct = observed.as_percent()
    buckets = ("buyer", "receptive", "uncertain", "skeptical")
    signed = {b: predicted_pct.get(b, 0.0) - obs_pct[b] for b in buckets}
    abs_err = {b: abs(signed[b]) for b in buckets}
    mae = sum(abs_err.values()) / len(buckets)
    max_err = max(abs_err.values())
    tvd = 0.5 * sum(abs_err.values()) / 100.0
    return {
        "observed_pct": obs_pct,
        "signed_err_pp": signed,
        "abs_err_pp": abs_err,
        "mae_pp": mae,
        "max_err_pp": max_err,
        "tvd": tvd,
        "observed_sample_size": observed.observed_sample_size,
        "noise_dropped_count": observed.noise,
        "labels_path_used": str(p),
        "labels_parse_warnings": list(outcome.parse_warnings),
    }


# ---------------------------------------------------------------------------
# Pillar extractors (12E.5D TASK 3)
# ---------------------------------------------------------------------------


def extract_skeptic_retention_from_diversity_health(
    dh: dict[str, Any] | None,
) -> float | None:
    """Return the skeptic-retention float in [0, 1] regardless of
    which key the Phase 12C diversity_health.json artifact uses.

    Phase 12C currently emits `skeptic_retention_rate`; the Phase
    12E.5A trajectory pillar contract calls it `skeptic_retention`.
    This extractor accepts both, returns None when neither exists.
    """
    if not dh:
        return None
    for key in ("skeptic_retention", "skeptic_retention_rate"):
        v = dh.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def extract_hard_resistant_count_from_diversity_health(
    dh: dict[str, Any] | None,
) -> int | None:
    if not dh:
        return None
    v = dh.get("hard_resistant_count")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


__all__ = [
    "LabelsFileError",
    "DurableLabels",
    "verify_labels_file_or_raise",
    "copy_labels_into_batch_dir",
    "score_run_against_durable_labels",
    "extract_skeptic_retention_from_diversity_health",
    "extract_hard_resistant_count_from_diversity_health",
]
