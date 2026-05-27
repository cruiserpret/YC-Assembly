"""Phase 12A.10E — Persistent evidence snapshots.

Snapshots preserve the exact retrieval + scoring + planning context
used by a single live-founder-brief run, so future repeated runs on
the same product brief can reuse the SAME evidence pool without
re-hitting Tavily / Firecrawl. This is the calibration-stability
substrate that Phase 12A.10C variance results showed we need before
Phase 12C scaling.

CRITICAL: a snapshot is NOT a cached prediction. Predictions are
generated downstream of the snapshot, by the simulator + cascade,
on every run. The snapshot only fixes the EVIDENCE INPUT
conditions — not the buyer/receptive/uncertain/skeptical
proportions, which remain unvalidated until scored against
real-world outcomes.

Storage: durable JSON files under
  apps/api/_audit/evidence_snapshots/<snapshot_id>.json

Designed so the same schema can graduate to a real
`evidence_snapshots` table in a future Alembic migration without
breaking the on-disk audit format. Every field that would become a
column is already a top-level key in the JSON envelope.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# -----------------------------------------------------------------
# Storage location. _AUDIT_SNAPSHOTS_DIR exists alongside live_runs.
# -----------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
# this file lives at: apps/api/src/assembly/calibration/<this>.py
# parents[3] resolves to apps/api/ — the location of `_audit/`.
_APPS_API_DIR = _THIS_FILE.parents[3]
_AUDIT_SNAPSHOTS_DIR = _APPS_API_DIR / "_audit" / "evidence_snapshots"


def snapshots_dir() -> Path:
    """Return (creating if needed) the on-disk snapshots directory."""
    _AUDIT_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIT_SNAPSHOTS_DIR


# -----------------------------------------------------------------
# Brief normalization + hashing
# -----------------------------------------------------------------

_BRIEF_HASHABLE_KEYS: tuple[str, ...] = (
    "product_name",
    "product_description",
    "price_or_price_structure",
    "launch_geography",
    "target_customers",
    "competitors_or_alternatives",
    "constraints",
    "launch_state",
    "report_depth",
    "category_hint",
    "optional_context",
    "simulation_question",
)


def _norm_str(s: Any) -> str:
    """Whitespace-collapse + lowercase + strip for hashing."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return " ".join(s.split()).strip().lower()


def normalize_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized brief copy: only the canonical keys,
    string values whitespace-collapsed + lowercased, list values
    sorted (since order shouldn't change the brief's identity).
    Used for `normalized_brief_hash`."""
    out: dict[str, Any] = {}
    for k in _BRIEF_HASHABLE_KEYS:
        v = brief.get(k)
        if v is None:
            out[k] = None
        elif isinstance(v, list):
            out[k] = sorted(_norm_str(x) for x in v)
        elif isinstance(v, (dict,)):
            out[k] = {kk: _norm_str(vv) for kk, vv in sorted(v.items())}
        else:
            out[k] = _norm_str(v)
    return out


def compute_raw_brief_hash(brief: dict[str, Any]) -> str:
    """SHA256 of the exact brief JSON (sorted keys, no
    normalization). Two byte-identical briefs produce the same
    hash; reordering keys or changing whitespace changes the hash.

    Used for byte-level audit. For matching "is this the same
    product?" use `compute_normalized_brief_hash` instead."""
    payload = json.dumps(
        brief, sort_keys=True, separators=(",", ":"), default=str,
    )
    return "sha256:" + hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()


def compute_normalized_brief_hash(brief: dict[str, Any]) -> str:
    """SHA256 of the normalized brief. Stable across cosmetic
    edits (whitespace, key order, case). Two operators who fill
    the same brief slightly differently will get the same hash."""
    norm = normalize_brief(brief)
    payload = json.dumps(
        norm, sort_keys=True, separators=(",", ":"), default=str,
    )
    return "sha256:" + hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()


# -----------------------------------------------------------------
# Snapshot Pydantic models
# -----------------------------------------------------------------

SnapshotStatus = Literal["active", "superseded", "invalidated"]
SnapshotSource = Literal["live_retrieval", "manually_supplied"]


class EvidenceSnapshotProvenance(BaseModel):
    """One retrieved evidence item, captured at the
    point it entered the pipeline. Items are kept as raw dicts in
    the orchestration ctx; this model is just a shape hint and
    field reference. Stored as `dict[str, Any]` in the snapshot
    envelope to avoid coupling to internal provider schemas."""

    model_config = ConfigDict(extra="allow")

    url: str | None = None
    title: str | None = None
    snippet: str | None = None
    provider: str | None = None
    score: float | None = None


class EvidenceSnapshot(BaseModel):
    """The full snapshot envelope. Persisted as a single JSON file.

    Calibration-status semantics: NEVER mark a snapshot's
    associated prediction as 'validated' here. This object only
    describes the input-side context. Prediction validation lives
    in a separate `calibration_status` field on the AssemblyRun /
    outcome_observations row (Phase 12A.10E task 2)."""

    model_config = ConfigDict(extra="allow")

    evidence_snapshot_id: str
    snapshot_hash: str
    brief_hash: str
    normalized_brief_hash: str

    product_name: str | None = None
    category_hint: str | None = None
    launch_state: str | None = None

    created_at: str
    simulator_version: str | None = None
    prompt_version: str | None = None
    cascade_version: str | None = None
    source: SnapshotSource = "live_retrieval"
    status: SnapshotStatus = "active"

    retrieval_provider_metadata: dict[str, Any] = Field(default_factory=dict)
    retrieval_queries: list[str] = Field(default_factory=list)
    raw_result_count: int = 0
    accepted_evidence_count: int = 0

    accepted_evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    raw_evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    rejected_evidence_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_quality_summary: dict[str, Any] = Field(default_factory=dict)

    anchor_plan: dict[str, Any] = Field(default_factory=dict)

    notes: str | None = None


def _compute_snapshot_hash(snap_dict: dict[str, Any]) -> str:
    """Stable hash of the input-side fields. EXCLUDES
    evidence_snapshot_id and snapshot_hash itself so the same
    content always produces the same hash regardless of id."""
    blob = {
        k: snap_dict.get(k)
        for k in (
            "brief_hash", "normalized_brief_hash",
            "retrieval_provider_metadata", "retrieval_queries",
            "accepted_evidence_items", "raw_evidence_items",
            "anchor_plan",
        )
    }
    payload = json.dumps(
        blob, sort_keys=True, separators=(",", ":"), default=str,
    )
    return "sha256:" + hashlib.sha256(
        payload.encode("utf-8"),
    ).hexdigest()


def _generate_snapshot_id(normalized_brief_hash: str) -> str:
    """Format: evsnap_<first8 of normalized hash>_<6 random hex>.
    Groups snapshots from the same product together while keeping
    each snapshot uniquely identifiable."""
    h_short = normalized_brief_hash.split(":")[-1][:8]
    return f"evsnap_{h_short}_{secrets.token_hex(3)}"


# -----------------------------------------------------------------
# Snapshot create / save / load
# -----------------------------------------------------------------


def build_snapshot_from_pipeline_ctx(
    *,
    brief: dict[str, Any],
    retrieval_audit: dict[str, Any],
    quality_audit: dict[str, Any],
    accepted_evidence: list[dict[str, Any]],
    raw_evidence: list[dict[str, Any]] | None = None,
    anchor_plan: dict[str, Any] | None = None,
    simulator_version: str | None = None,
    prompt_version: str | None = None,
    cascade_version: str | None = None,
    source: SnapshotSource = "live_retrieval",
    notes: str | None = None,
) -> EvidenceSnapshot:
    """Build a snapshot from the live-pipeline ctx at the end of a
    successful run. All non-evidence fields come from the brief and
    the retrieval/quality audits; the snapshot id and hash are
    derived deterministically from the brief."""
    norm_hash = compute_normalized_brief_hash(brief)
    raw_hash = compute_raw_brief_hash(brief)
    snap_id = _generate_snapshot_id(norm_hash)
    queries = retrieval_audit.get("queries") or []
    if not queries and "per_provider_query_count" in retrieval_audit:
        queries = [
            f"<{p}:{n}>" for p, n in (
                retrieval_audit.get("per_provider_query_count") or {}
            ).items()
        ]
    snap_dict = {
        "evidence_snapshot_id": snap_id,
        "snapshot_hash": "sha256:pending",
        "brief_hash": raw_hash,
        "normalized_brief_hash": norm_hash,
        "product_name": brief.get("product_name"),
        "category_hint": brief.get("category_hint"),
        "launch_state": brief.get("launch_state"),
        "created_at": datetime.now(UTC).isoformat(),
        "simulator_version": simulator_version,
        "prompt_version": prompt_version,
        "cascade_version": cascade_version,
        "source": source,
        "status": "active",
        "retrieval_provider_metadata": {
            k: v for k, v in retrieval_audit.items()
            if k in (
                "providers_configured", "providers_attempted",
                "providers_skipped", "provider_skip_reasons",
                "per_provider_query_count", "per_provider_raw_count",
                "tier_1_raw_count", "tier_2_raw_count",
                "any_retrieval_provider_configured",
            )
        },
        "retrieval_queries": queries,
        "raw_result_count": int(retrieval_audit.get("raw_result_count", 0)),
        "accepted_evidence_count": len(accepted_evidence),
        "accepted_evidence_items": list(accepted_evidence),
        "raw_evidence_items": list(raw_evidence or []),
        "rejected_evidence_summary": {
            "rejected_count": quality_audit.get("rejected_count", 0),
            "rejection_counts": (
                quality_audit.get("rejection_counts") or {}
            ),
        },
        "evidence_quality_summary": {
            "raw_count": quality_audit.get("raw_count", 0),
            "accepted_count": quality_audit.get("accepted_count", 0),
            "rejected_count": quality_audit.get("rejected_count", 0),
        },
        "anchor_plan": dict(anchor_plan or {}),
        "notes": notes,
    }
    snap_dict["snapshot_hash"] = _compute_snapshot_hash(snap_dict)
    return EvidenceSnapshot(**snap_dict)


def save_snapshot(snap: EvidenceSnapshot) -> Path:
    """Write the snapshot envelope to its on-disk path. The path
    is `<snapshots_dir>/<evidence_snapshot_id>.json`. Returns the
    path. Refuses to overwrite an existing file with a different
    snapshot_hash — that would be silent history rewriting."""
    target = snapshots_dir() / f"{snap.evidence_snapshot_id}.json"
    if target.exists():
        existing = json.loads(target.read_text())
        if existing.get("snapshot_hash") != snap.snapshot_hash:
            raise ValueError(
                f"refusing to overwrite snapshot {snap.evidence_snapshot_id} "
                "— existing hash differs from new hash. "
                "Snapshots are append-only audit artifacts."
            )
    target.write_text(
        json.dumps(snap.model_dump(mode="json"), indent=2, default=str)
    )
    return target


def load_snapshot(snapshot_id: str) -> EvidenceSnapshot:
    """Load an evidence snapshot by id. Raises FileNotFoundError if
    the snapshot does not exist. Raises ValueError if the on-disk
    snapshot_hash does not match the content (tamper check)."""
    path = snapshots_dir() / f"{snapshot_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"evidence snapshot not found: {snapshot_id} "
            f"(expected at {path})"
        )
    data = json.loads(path.read_text())
    stored_hash = data.get("snapshot_hash")
    recomputed = _compute_snapshot_hash(data)
    if stored_hash != recomputed:
        raise ValueError(
            f"evidence snapshot {snapshot_id} tamper check failed: "
            f"stored hash {stored_hash!r} != recomputed {recomputed!r}"
        )
    return EvidenceSnapshot(**data)


def check_brief_matches_snapshot(
    brief: dict[str, Any],
    snap: EvidenceSnapshot,
    *,
    require_exact: bool = False,
) -> tuple[bool, str]:
    """Verify the supplied brief matches the snapshot's recorded
    brief. Returns (matches, reason). When require_exact is False
    (the default), only the normalized_brief_hash is checked —
    cosmetic edits are allowed."""
    if require_exact:
        h = compute_raw_brief_hash(brief)
        if h != snap.brief_hash:
            return False, (
                f"raw_brief_hash mismatch: brief={h} "
                f"snapshot={snap.brief_hash}"
            )
        return True, "raw_brief_hash matches"
    h = compute_normalized_brief_hash(brief)
    if h != snap.normalized_brief_hash:
        return False, (
            f"normalized_brief_hash mismatch: brief={h} "
            f"snapshot={snap.normalized_brief_hash}"
        )
    return True, "normalized_brief_hash matches"
