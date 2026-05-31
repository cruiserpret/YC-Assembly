"""Phase 15I — build a validation-ledger PENDING case from a completed run.

The prediction-lock bridge: given a completed Assembly run, produce a
validation-ledger ``pending`` case skeleton that records WHAT Assembly
predicted (the four market-reaction buckets) and the auditable prediction lock,
WITHOUT inventing any observed outcome. The observed outcome is added later, by
a human, once the market actually reacts — only then can the case be scored.

What this module does:
  - resolves the run's durable artifact dir via Phase 14C
    ``artifact_paths.run_artifact_dir`` (honours ASSEMBLY_ARTIFACT_ROOT),
  - reads ``founder_report.json`` and folds its ``intent_distribution`` into the
    four canonical ledger buckets using the conservative, routing-independent
    label->bucket mapping from ``calibration.report_extractor`` (reused, not
    re-derived) — and REFUSES to lock a meaningless flat 25/25/25/25 prior,
  - reads ``evidence_snapshot.json`` (best-effort) for the brief/snapshot hashes
    and lock timestamp,
  - computes a deterministic ``prediction_hash`` (see prediction_lock.py),
  - assembles a ``pending`` ValidationCase with predicted set, observed=None,
    used_for_holdout=True, used_for_training=False.

It is read-only over run artifacts: NO outcome is read or invented, NO forecast
is computed or changed, NO calibration is applied, NO LLM / network / DB is
touched, and missing artifacts never crash with an opaque traceback (they raise
a clear error, or — under ``allow_partial`` — yield a flagged partial skeleton).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from assembly.artifact_paths import run_artifact_dir
from assembly.calibration.report_extractor import (
    extract_bucket_counts_from_intent_distribution,
)
from assembly.validation_ledger.prediction_lock import compute_prediction_hash
from assembly.validation_ledger.schema import ValidationCase

_REPORT_FILE = "founder_report.json"
_SNAPSHOT_FILE = "evidence_snapshot.json"

# calibration short bucket name -> canonical ledger bucket name
_SHORT_TO_LONG = {
    "buyer": "buyer_action_positive",
    "receptive": "receptive",
    "uncertain": "uncertain_proof_needed",
    "skeptical": "skeptical_resistant",
}


class RunArtifactsMissingError(FileNotFoundError):
    """Raised when a run's prediction artifacts are absent and the caller did
    not opt into a partial skeleton."""


class RunPredictionUnusableError(ValueError):
    """Raised when a run's report has no usable (non-flat-prior) prediction and
    the caller did not opt into a partial skeleton."""


def _locate_intent_distribution(report: dict[str, Any]) -> dict | None:
    """Return the report's intent_distribution block, tolerant to schema drift.

    Mirrors calibration.report_extractor's priority (synthetic_intent_snapshot
    -> intent_snapshot -> executive_summary -> top-level) but is pinned to the
    intent_distribution (label) view only, so the locked prediction is
    deterministic and independent of the ASSEMBLY_INTENT_SIGNAL_ROUTING_ENABLED
    runtime flag.
    """
    for key in ("synthetic_intent_snapshot", "intent_snapshot", "executive_summary"):
        block = report.get(key)
        if isinstance(block, dict):
            dist = block.get("intent_distribution")
            if isinstance(dist, dict) and dist:
                return dist
    top = report.get("intent_distribution")
    if isinstance(top, dict) and top:
        return top
    return None


def _predicted_from_report(report: dict[str, Any]) -> tuple[dict[str, float] | None, list[str]]:
    """Extract the four canonical predicted proportions (percentage points)
    from a founder_report dict, or (None, warnings) if not usable."""
    warnings: list[str] = []
    intent_dist = _locate_intent_distribution(report)
    if not intent_dist:
        return None, ["no intent_distribution block found in founder_report.json"]
    counts = extract_bucket_counts_from_intent_distribution(intent_dist)
    warnings.extend(counts.warnings)
    if counts.total <= 0:
        return None, [
            *warnings,
            "intent_distribution has zero usable votes — refusing to lock a "
            "flat 25/25/25/25 prior as a 'prediction'",
        ]
    frac = counts.as_distribution()  # short keys, fractions summing to 1.0
    predicted = {
        long: round(frac[short] * 100.0, 4) for short, long in _SHORT_TO_LONG.items()
    }
    return predicted, warnings


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable artifact is non-fatal
        return None


def build_pending_case_from_run(
    run_id: str,
    *,
    source_type: str = "unknown",
    product_category: str = "unknown",
    product_name: str | None = None,
    launch_stage: str = "unknown",
    case_id: str | None = None,
    locked_at: str | None = None,
    date_run: str | None = None,
    leakage_risk: str = "low",
    allow_partial: bool = False,
    run_dir: str | Path | None = None,
) -> tuple[ValidationCase, list[str]]:
    """Build a ``pending`` (or, under allow_partial, ``partial``) ValidationCase
    from a completed run's artifacts. Returns ``(case, warnings)``.

    Never invents an observed outcome (``observed`` is always None). Defaults to
    a BLIND holdout case (used_for_holdout=True, used_for_training=False).

    Raises ``RunArtifactsMissing`` if founder_report.json is absent, or
    ``RunPredictionUnusableError`` if it carries no usable prediction — unless
    ``allow_partial`` is set, in which case a flagged partial skeleton is built.
    """
    warnings: list[str] = []
    rid = str(run_id)
    rdir = Path(run_dir) if run_dir is not None else run_artifact_dir(rid)
    report_path = rdir / _REPORT_FILE

    report: dict[str, Any] | None = None
    if report_path.exists():
        report = _read_json(report_path)
        if report is None:
            warnings.append(f"founder_report.json at {report_path} is unreadable")
    if report is None:
        if not allow_partial:
            raise RunArtifactsMissingError(
                f"founder_report.json missing/unreadable for run_id={rid} at {rdir} "
                "— cannot lock a prediction. Re-run with allow_partial=True to "
                "store a flagged partial skeleton instead."
            )
        warnings.append(f"founder_report.json missing/unreadable at {rdir} (partial)")

    # Sanity: the artifact should be for this run.
    if report is not None and report.get("run_id") and str(report.get("run_id")) != rid:
        warnings.append(
            f"founder_report.run_id={report.get('run_id')!r} != requested run_id={rid!r}"
        )

    predicted: dict[str, float] | None = None
    if report is not None:
        predicted, pw = _predicted_from_report(report)
        warnings.extend(pw)
        if predicted is None and not allow_partial:
            raise RunPredictionUnusableError(
                f"run_id={rid}: no usable prediction in founder_report.json "
                f"({pw[-1] if pw else 'unknown reason'}). Use allow_partial=True "
                "to store a partial skeleton without a locked prediction."
            )

    # Best-effort evidence snapshot (brief/snapshot hashes + lock timestamp).
    brief_hash = ev_snapshot_id = ev_snapshot_hash = snap_completed_at = None
    snap_path = rdir / _SNAPSHOT_FILE
    if snap_path.exists():
        snap = _read_json(snap_path)
        if snap is None:
            warnings.append(f"evidence_snapshot.json at {snap_path} is unreadable")
        else:
            brief_hash = snap.get("brief_hash")
            ev_snapshot_id = snap.get("evidence_snapshot_id")
            ev_snapshot_hash = snap.get("snapshot_hash")
            snap_completed_at = snap.get("completed_at")
    else:
        warnings.append(
            "evidence_snapshot.json missing — brief_hash/evidence_snapshot "
            "fields will be null (a run may legitimately have none)"
        )

    # Lock timestamp: explicit arg wins, else the snapshot's completed_at. We do
    # NOT fall back to a volatile file mtime for the lock timestamp because it
    # feeds the prediction_hash and must be stable/auditable.
    effective_locked_at = locked_at or snap_completed_at
    if predicted is not None and effective_locked_at is None and not allow_partial:
        raise RunPredictionUnusableError(
            f"run_id={rid}: no lock timestamp available (no evidence_snapshot.json "
            "completed_at and no explicit locked_at) — refusing to create an "
            "un-auditable lock. Pass locked_at, or use allow_partial=True."
        )

    # product_name + model version from the report (DB-free).
    if product_name is None and report is not None:
        product_name = ((report.get("product_brief") or {}).get("product_name")) or None
    if not product_name:
        product_name = f"run-{rid[:8]}"
    report_schema_version = report.get("schema_version") if report else None

    # date_run is metadata only (NOT hashed) — a soft fallback is fine.
    effective_date_run = (
        date_run
        or (effective_locked_at[:10] if effective_locked_at else None)
        or "unknown"
    )

    is_partial = predicted is None
    status = "partial" if is_partial else "pending"

    prediction_hash = None
    if predicted is not None:
        prediction_hash = compute_prediction_hash(
            run_id=rid,
            predicted=predicted,
            simulation_id=None,  # no first-class per-run simulation_id is persisted
            brief_hash=brief_hash,
            evidence_snapshot_id=ev_snapshot_id,
            evidence_snapshot_hash=ev_snapshot_hash,
            locked_prediction_created_at=effective_locked_at,
            model_version={"report_schema_version": report_schema_version},
        )

    note = (
        "Phase 15I auto-generated skeleton from a completed run. The OBSERVED "
        "outcome must be added later (by a human) before this case can be "
        "scored — no observed outcome is present and none was invented."
    )
    if is_partial:
        note += " PARTIAL: no usable locked prediction was extracted from the run artifacts."

    payload: dict[str, Any] = {
        "case_id": case_id or f"run_{rid}",
        "metadata": {
            "product_name": product_name,
            "source_type": source_type,
            "product_category": product_category,
            "launch_stage": launch_stage,
            "date_run": effective_date_run,
            "validation_status": status,
            "notes": note,
        },
        "prediction_lock": {
            "run_id": rid,
            "brief_hash": brief_hash,
            "evidence_snapshot_id": ev_snapshot_id,
            "evidence_snapshot_hash": ev_snapshot_hash,
            "prediction_hash": prediction_hash,
            "locked_prediction_created_at": effective_locked_at,
            "leakage_risk": leakage_risk,
            "clean_room_notes": (
                "Prediction derived from the run's founder_report.json "
                "intent_distribution via the conservative, routing-independent "
                "label->bucket mapping, and locked before any observed outcome. "
                "No outcome data was read."
            ),
        },
        "anti_overfit": {
            "used_for_training": False,
            "used_for_holdout": True,
            "notes": "Blind pending case auto-created from a locked run (Phase 15I).",
        },
    }
    if predicted is not None:
        payload["predicted"] = predicted
    # observed is intentionally omitted (None) — outcome added later.

    case = ValidationCase.model_validate(payload)
    return case, warnings
