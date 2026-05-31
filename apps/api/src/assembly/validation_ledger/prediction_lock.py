"""Phase 15I — deterministic prediction-lock hashing.

A *prediction lock* is the auditable fingerprint of what Assembly predicted for
a run, computed BEFORE any outcome is known. ``compute_prediction_hash`` turns
the locked prediction into a stable ``sha256:`` digest so a later reviewer can
prove the prediction was not changed after the outcome was observed.

The hash is deliberately PORTABLE and DETERMINISTIC:
  - it includes only content (run identity, the four predicted proportions,
    content hashes of the brief/evidence snapshot, the lock timestamp, and
    run-recorded model/version strings),
  - it NEVER includes volatile filesystem paths, machine-local roots, or the
    current wall-clock time (only the explicit lock timestamp),
  - predicted proportions are formatted to a fixed number of decimals before
    hashing so float repr drift (0.1 + 0.2) can never change the digest.

This mirrors the repo's existing hash idiom (calibration.evidence_snapshots /
calibration.blind_case_schema): ``json.dumps(payload, sort_keys=True,
separators=(",", ":"))`` then ``sha256`` hexdigest, ``sha256:``-prefixed.

Pure module: stdlib + the ledger's BUCKET_KEYS only. No LLM, no network, no DB,
no filesystem access.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from assembly.validation_ledger.metrics import BUCKET_KEYS

# Bumping this string is the ONLY supported way to change what the hash covers
# or how it is formatted — it guarantees an old digest can never silently
# collide with a new algorithm.
PREDICTION_HASH_SCHEMA_VERSION = "prediction_hash.v1"

# Percentage-point proportions are rounded/formatted to this many decimals
# before hashing. Pinned by the schema version above.
_HASH_DECIMALS = 4


def _fmt_proportion(value: float) -> str:
    """Format a percentage-point proportion to a fixed-decimal string so two
    numerically-equal predictions always hash identically. The ``+ 0.0``
    normalizes -0.0 to 0.0 (IEEE-754: -0.0 + 0.0 == +0.0) so a logically-zero
    bucket always formats as ``0.0000`` regardless of sign."""
    return f"{round(float(value), _HASH_DECIMALS) + 0.0:.{_HASH_DECIMALS}f}"


def canonical_prediction_payload(
    *,
    run_id: str,
    predicted: Mapping[str, float],
    simulation_id: str | None = None,
    brief_hash: str | None = None,
    evidence_snapshot_id: str | None = None,
    evidence_snapshot_hash: str | None = None,
    locked_prediction_created_at: str | None = None,
    model_version: Mapping[str, str | None] | None = None,
) -> dict:
    """Build the canonical, hash-ready payload for a locked prediction.

    ``predicted`` must contain the four canonical ledger buckets (BUCKET_KEYS),
    in percentage points. Every field key is always emitted (None when a source
    is unavailable) so the field set is stable across cases — two predictions
    with identical content always canonicalize identically regardless of which
    optional sources happened to be present.
    """
    missing = [k for k in BUCKET_KEYS if k not in predicted]
    if missing:
        raise ValueError(
            f"predicted is missing canonical bucket(s): {missing} "
            f"(need exactly {list(BUCKET_KEYS)})"
        )
    predicted_fmt = {k: _fmt_proportion(predicted[k]) for k in BUCKET_KEYS}
    return {
        "hash_schema_version": PREDICTION_HASH_SCHEMA_VERSION,
        "run_id": str(run_id),
        "simulation_id": None if simulation_id is None else str(simulation_id),
        "predicted": predicted_fmt,
        "brief_hash": brief_hash,
        "evidence_snapshot_id": evidence_snapshot_id,
        "evidence_snapshot_hash": evidence_snapshot_hash,
        "locked_prediction_created_at": locked_prediction_created_at,
        "model_version": dict(model_version) if model_version else None,
    }


def compute_prediction_hash(
    *,
    run_id: str,
    predicted: Mapping[str, float],
    simulation_id: str | None = None,
    brief_hash: str | None = None,
    evidence_snapshot_id: str | None = None,
    evidence_snapshot_hash: str | None = None,
    locked_prediction_created_at: str | None = None,
    model_version: Mapping[str, str | None] | None = None,
) -> str:
    """Return ``"sha256:" + sha256(canonical_payload)`` for a locked prediction.

    Deterministic and path-free: the same inputs always produce the same digest
    on any machine; no value derived from a filesystem path or the current time
    enters the digest.
    """
    payload = canonical_prediction_payload(
        run_id=run_id,
        predicted=predicted,
        simulation_id=simulation_id,
        brief_hash=brief_hash,
        evidence_snapshot_id=evidence_snapshot_id,
        evidence_snapshot_hash=evidence_snapshot_hash,
        locked_prediction_created_at=locked_prediction_created_at,
        model_version=model_version,
    )
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
