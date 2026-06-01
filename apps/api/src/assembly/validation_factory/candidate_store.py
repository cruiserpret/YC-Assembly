"""Phase 15J — candidate store: read/write CandidateCase files (ISOLATED).

Candidates live as one JSON file per candidate under
``apps/api/validation_cases/candidates/``. That directory is deliberately ABSENT
from ``manifest.json``, so the validation-ledger loaders never see it. This
module reads/writes ONLY candidate files; it never touches the ledger. Every
write honours ``dry_run`` (no filesystem change). No LLM, no network, no DB.
"""
from __future__ import annotations

import json
from pathlib import Path

from assembly.validation_factory.candidate_schema import CandidateCase

# apps/api/validation_cases/candidates/
#   this file: apps/api/src/assembly/validation_factory/candidate_store.py
#   parents[3] -> apps/api
DEFAULT_CANDIDATES_DIR = (
    Path(__file__).resolve().parents[3] / "validation_cases" / "candidates"
)


def _safe_candidate_id(candidate_id: str) -> str:
    cid = str(candidate_id)
    if not cid or cid in (".", "..") or "/" in cid or "\\" in cid or ".." in cid:
        raise ValueError(f"unsafe candidate_id for a file path: {candidate_id!r}")
    return cid


def _dir(candidates_dir: str | Path | None) -> Path:
    return Path(candidates_dir) if candidates_dir is not None else DEFAULT_CANDIDATES_DIR


def candidate_path(candidate_id: str, candidates_dir: str | Path | None = None) -> Path:
    return _dir(candidates_dir) / f"{_safe_candidate_id(candidate_id)}.json"


def load_candidate(
    candidate_id: str, candidates_dir: str | Path | None = None
) -> CandidateCase:
    p = candidate_path(candidate_id, candidates_dir)
    if not p.exists():
        raise FileNotFoundError(f"candidate not found: {p}")
    return CandidateCase.model_validate(json.loads(p.read_text(encoding="utf-8")))


def load_all_candidates(
    candidates_dir: str | Path | None = None,
) -> list[CandidateCase]:
    """Load every candidate JSON in the directory (sorted). Ignores non-JSON
    files (e.g. README.md). Returns [] if the directory does not exist yet."""
    d = _dir(candidates_dir)
    if not d.exists():
        return []
    out: list[CandidateCase] = []
    for f in sorted(d.glob("*.json")):
        out.append(CandidateCase.model_validate(json.loads(f.read_text(encoding="utf-8"))))
    return out


def save_candidate(
    candidate: CandidateCase,
    candidates_dir: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> Path:
    """Write the candidate to ``<dir>/<candidate_id>.json``. With ``dry_run`` it
    resolves and returns the path but writes NOTHING."""
    p = candidate_path(candidate.candidate_id, candidates_dir)
    if dry_run:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(candidate.model_dump(mode="json", exclude_none=True), indent=2) + "\n",
        encoding="utf-8",
    )
    return p
